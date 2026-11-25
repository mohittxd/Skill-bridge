"""Local ESCO career recommendations with cached Sentence Transformer vectors."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Iterable

import numpy as np


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
RELATED_SKILL_THRESHOLD = 0.76
_MODEL = None
_MODEL_LOCK = threading.Lock()
_SKILL_ALIASES = {
    "js": "javascript",
    "javascript": "javascript",
    "microsoft excel": "excel",
    "ms excel": "excel",
    "powerbi": "power bi",
    "microsoft power bi": "power bi",
    "python programming": "python",
    "postgresql": "postgres",
    "postgres": "postgres",
}
_NON_WORD = re.compile(r"[^a-z0-9+#]+")
_WORD = re.compile(r"[a-z0-9+#]+")


def _value(row: Any, key: str, default: str = "") -> str:
    value = row[key] if key in row.keys() else default
    return str(value or "").strip()


def _labels(value: str | Iterable[str]) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            parsed = re.split(r"[|\n]", value)
    else:
        parsed = list(value)
    return list(dict.fromkeys(
        str(label).strip() for label in parsed
        if str(label).strip()
    ))


def normalize_skill(value: str) -> str:
    normalized = _NON_WORD.sub(" ", unicodedata.normalize("NFKC", value).casefold())
    normalized = " ".join(normalized.split())
    return _SKILL_ALIASES.get(normalized, normalized)


def _occupation_documents(
    occupations: Iterable[Any],
    skills: Iterable[Any],
    relations: Iterable[Any],
) -> dict[str, str]:
    skill_by_uri = {str(row["uri"]): row for row in skills}
    relation_skills: dict[str, dict[str, list[str]]] = defaultdict(
        lambda: {"essential": [], "optional": []}
    )
    for relation in relations:
        relation_type = _value(relation, "relation_type").casefold()
        if relation_type in {"essential", "optional"}:
            skill = skill_by_uri.get(str(relation["skill_uri"]))
            if skill:
                relation_skills[str(relation["occupation_uri"])][relation_type].append(
                    _value(skill, "preferred_label")
                )

    documents: dict[str, str] = {}
    for occupation in occupations:
        uri = str(occupation["uri"])
        labels = _labels(_value(occupation, "alt_labels", "[]"))
        essential = relation_skills[uri]["essential"]
        optional = relation_skills[uri]["optional"]
        text = " ".join(filter(None, (
            _value(occupation, "preferred_label"),
            "Alternative titles: " + ", ".join(labels) if labels else "",
            _value(occupation, "description"),
            "Essential skills: " + ", ".join(essential) if essential else "",
            "Optional skills: " + ", ".join(optional) if optional else "",
        )))
        documents[uri] = text
    return documents


def _skill_documents(skills: Iterable[Any]) -> dict[str, str]:
    return {
        str(skill["uri"]): " ".join(filter(None, (
            "ESCO skill:",
            _value(skill, "preferred_label"),
            "Alternative terms: " + ", ".join(_labels(_value(skill, "alt_labels", "[]"))),
            _value(skill, "description"),
        )))
        for skill in skills
    }


def _load_model(device: str | None = None):
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                try:
                    from sentence_transformers import SentenceTransformer
                except ImportError as exc:
                    raise RuntimeError(
                        "Sentence Transformers is not installed. Run "
                        "`python -m pip install -r requirements.txt` from backend/."
                    ) from exc
                _MODEL = SentenceTransformer(MODEL_NAME, device=device)
    return _MODEL


def _encode(model: Any, texts: list[str]) -> np.ndarray:
    # Cache small profile/query batches, never the entire preprocessing corpus.
    return _encode_cached(model, tuple(texts)) if len(texts) <= 64 else _encode_uncached(model, texts)

@lru_cache(maxsize=128)
def _encode_cached(model, texts):
    return _encode_uncached(model, list(texts))


def _encode_uncached(model: Any, texts: list[str]) -> np.ndarray:
    encoded = model.encode(
        texts,
        batch_size=64,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    vectors = np.asarray(encoded, dtype=np.float32)
    if vectors.ndim != 2 or vectors.shape[0] != len(texts):
        raise ValueError("The embedding model returned an invalid vector matrix.")
    return np.ascontiguousarray(vectors)


def precompute_catalog_embeddings(
    connection: sqlite3.Connection,
    model: Any = None,
    device: str | None = None,
) -> dict[str, int]:
    """Create or refresh cached vectors for all imported ESCO occupations and skills."""
    occupations = connection.execute(
        "SELECT uri, preferred_label, alt_labels, description FROM esco_occupations ORDER BY uri"
    ).fetchall()
    skills = connection.execute(
        "SELECT uri, preferred_label, alt_labels, description FROM esco_skills ORDER BY uri"
    ).fetchall()
    relations = connection.execute(
        """
        SELECT occupation_uri, skill_uri, relation_type
        FROM esco_occupation_skill_relations
        ORDER BY occupation_uri, skill_uri
        """
    ).fetchall()
    if not occupations or not skills or not relations:
        raise ValueError("Import ESCO occupations, skills, and relations before embedding them.")

    documents = {
        ("occupation", uri): text
        for uri, text in _occupation_documents(occupations, skills, relations).items()
    }
    documents.update({
        ("skill", uri): text
        for uri, text in _skill_documents(skills).items()
    })
    hashes = {
        identity: hashlib.sha256(text.encode("utf-8")).hexdigest()
        for identity, text in documents.items()
    }
    existing = {
        (str(row["concept_type"]), str(row["concept_uri"])): str(row["content_hash"])
        for row in connection.execute(
            """
            SELECT concept_type, concept_uri, content_hash
            FROM esco_concept_embeddings
            WHERE model_name = ?
            """,
            (MODEL_NAME,),
        )
    }
    pending = [
        identity for identity in documents
        if existing.get(identity) != hashes[identity]
    ]
    model = model or _load_model(device)
    if pending:
        vectors = _encode(model, [documents[identity] for identity in pending])
        with connection:
            connection.executemany(
                """
                INSERT INTO esco_concept_embeddings(
                    concept_type, concept_uri, model_name, content_hash, dimensions, vector
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(concept_type, concept_uri, model_name) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    dimensions = excluded.dimensions,
                    vector = excluded.vector
                """,
                [
                    (
                        identity[0],
                        identity[1],
                        MODEL_NAME,
                        hashes[identity],
                        int(vectors[index].shape[0]),
                        vectors[index].tobytes(),
                    )
                    for index, identity in enumerate(pending)
                ],
            )
    with connection:
        connection.execute(
            """
            DELETE FROM esco_concept_embeddings
            WHERE model_name = ? AND (concept_type, concept_uri) NOT IN (
                SELECT 'occupation', uri FROM esco_occupations
                UNION ALL
                SELECT 'skill', uri FROM esco_skills
            )
            """,
            (MODEL_NAME,),
        )
        connection.executemany(
            """
            INSERT INTO esco_catalog_metadata(key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("embedding_model", MODEL_NAME),
                ("embeddings_ready", "true"),
                ("embeddings_prepared_at", datetime.now(timezone.utc).isoformat()),
            ),
        )
    return {
        "occupation_embeddings": len(occupations),
        "skill_embeddings": len(skills),
        "computed": len(pending),
    }


def cached_catalog_embeddings(
    connection: sqlite3.Connection,
    occupation_uris: set[str],
    skill_uris: set[str],
) -> dict[str, dict[str, np.ndarray]]:
    vectors: dict[str, dict[str, np.ndarray]] = {
        "occupation": {},
        "skill": {},
    }
    for row in connection.execute(
        """
        SELECT concept_type, concept_uri, dimensions, vector
        FROM esco_concept_embeddings
        WHERE model_name = ?
        """,
        (MODEL_NAME,),
    ):
        concept_type = str(row["concept_type"])
        uri = str(row["concept_uri"])
        expected = occupation_uris if concept_type == "occupation" else skill_uris
        if uri not in expected:
            continue
        vector = np.frombuffer(row["vector"], dtype=np.float32)
        if vector.size != int(row["dimensions"]):
            continue
        vectors[concept_type][uri] = vector
    if set(vectors["occupation"]) != occupation_uris or set(vectors["skill"]) != skill_uris:
        raise RuntimeError(
            "ESCO embeddings are missing or out of date. Run "
            "`python prepare_esco_embeddings.py` from backend/."
        )
    dimensions = {vector.size for kind in vectors.values() for vector in kind.values()}
    if len(dimensions) != 1:
        raise RuntimeError("ESCO cached embeddings use inconsistent vector dimensions.")
    return vectors


def _profile_skills(profile_skills: dict[str, dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(
        str(skill.get("name", "")).strip()
        for skill in profile_skills.values()
        if str(skill.get("name", "")).strip()
    ))


def _preference_similarity(title: str, alternate_titles: list[str], interests: list[str]) -> float:
    if not interests:
        return 0.0
    candidates = [title, *alternate_titles]
    best = 0.0
    for candidate in candidates:
        candidate_norm = normalize_skill(candidate)
        candidate_tokens = set(_WORD.findall(candidate_norm))
        for interest in interests:
            interest_norm = normalize_skill(interest)
            interest_tokens = set(_WORD.findall(interest_norm))
            if not interest_tokens:
                continue
            if candidate_norm == interest_norm:
                similarity = 1.0
            elif interest_norm in candidate_norm or candidate_norm in interest_norm:
                similarity = 0.92
            else:
                overlap = len(candidate_tokens & interest_tokens) / len(interest_tokens)
                similarity = overlap
            best = max(best, similarity)
    return best


def _search_match(
    query: str,
    occupation: Any,
    occupation_skills: list[tuple[Any, Any]],
) -> float | None:
    if not query:
        return 0.0
    query_norm = normalize_skill(query)
    # Common vacancy titles mapped to actual labels in the local ESCO catalog.
    query_norm = {
        'full stack developer': 'web developer',
        'fullstack developer': 'web developer',
        'ui ux designer': 'user interface designer',
        'ux designer': 'user experience analyst',
    }.get(query_norm, query_norm)
    query_tokens = set(_WORD.findall(query_norm))
    if not query_tokens:
        return None
    title = _value(occupation, "preferred_label")
    title_norm = normalize_skill(title)
    alternatives = _labels(_value(occupation, "alt_labels", "[]"))
    if query_norm == title_norm:
        return 1.0
    if query_norm in title_norm:
        return 0.95
    if any(query_norm == normalize_skill(label) for label in alternatives):
        return 0.92
    if any(query_norm in normalize_skill(label) for label in alternatives):
        return 0.88
    title_tokens = set(_WORD.findall(title_norm))
    if query_tokens.issubset(title_tokens):
        return 0.85
    alternative_tokens = {
        token for label in alternatives for token in _WORD.findall(normalize_skill(label))
    }
    if query_tokens.issubset(alternative_tokens):
        return 0.80
    role_skill_tokens = {
        token
        for skill, _ in occupation_skills
        for label in [str(skill["preferred_label"]), *_labels(skill["alt_labels"])]
        for token in _WORD.findall(normalize_skill(label))
    }
    if query_tokens.issubset(role_skill_tokens):
        return 0.65
    return None


def recommend_career_roles(
    occupations: Iterable[Any],
    skills: Iterable[Any],
    relations: Iterable[Any],
    embeddings: dict[str, dict[str, np.ndarray]],
    profile_skills: dict[str, dict[str, Any]],
    *,
    education: str = "",
    work_experience: str = "",
    interests: list[str] | None = None,
    query: str = "",
    limit: int = 10,
    model: Any = None,
) -> dict[str, Any]:
    interests = [interest.strip() for interest in (interests or []) if interest.strip()]
    occupations = list(occupations)
    skills = list(skills)
    relations = list(relations)
    skill_by_uri = {str(row["uri"]): row for row in skills}
    occupation_skills: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
    for relation in relations:
        occupation = str(relation["occupation_uri"])
        skill = skill_by_uri.get(str(relation["skill_uri"]))
        if skill:
            occupation_skills[occupation].append((skill, relation))

    names = _profile_skills(profile_skills)
    profile_text = " ".join(filter(None, (
        "Employee confirmed skills: " + ", ".join(names) if names else "",
        "Education: " + education.strip() if education.strip() else "",
        "Work experience: " + work_experience.strip() if work_experience.strip() else "",
        "Career interests: " + ", ".join(interests) if interests else "",
    )))
    has_profile = bool(profile_text)
    if not has_profile and not query.strip():
        return {
            "roles": [],
            "considered": len(occupations),
            "result_count": 0,
            "profile_skill_count": 0,
            "query": "",
            "message": (
                "Add confirmed skills, education, experience, or career interests "
                "to get recommendations."
            ),
        }
    model = model or (_load_model() if has_profile else None)
    profile_vector = (
        _encode(model, [profile_text])[0]
        if has_profile
        else None
    )

    label_to_uri: dict[str, str] = {}
    for skill in skills:
        uri = str(skill["uri"])
        for label in [_value(skill, "preferred_label"), *_labels(_value(skill, "alt_labels", "[]"))]:
            key = normalize_skill(label)
            if key:
                label_to_uri.setdefault(key, uri)

    exact_skill_map: dict[str, str] = {}
    unknown_names: list[str] = []
    for name in names:
        uri = label_to_uri.get(normalize_skill(name))
        if uri:
            exact_skill_map[name] = uri
        else:
            unknown_names.append(name)

    related_skill_map: dict[str, tuple[str, float]] = {}
    if unknown_names and embeddings["skill"]:
        unknown_vectors = _encode(
            model, [f"ESCO skill: {name}" for name in unknown_names]
        )
        indexed_skill_uris = list(embeddings["skill"])
        skill_matrix = np.stack([embeddings["skill"][uri] for uri in indexed_skill_uris])
        similarities = unknown_vectors @ skill_matrix.T
        for index, name in enumerate(unknown_names):
            best_index = int(np.argmax(similarities[index]))
            similarity = float(similarities[index, best_index])
            if similarity >= RELATED_SKILL_THRESHOLD:
                related_skill_map[name] = (
                    indexed_skill_uris[best_index],
                    similarity,
                )

    recognized_by_uri: dict[str, list[tuple[str, bool, float]]] = defaultdict(list)
    for name, uri in exact_skill_map.items():
        recognized_by_uri[uri].append((name, False, 1.0))
    for name, (uri, similarity) in related_skill_map.items():
        recognized_by_uri[uri].append((name, True, similarity))

    ranked: list[dict[str, Any]] = []
    for occupation in occupations:
        uri = str(occupation["uri"])
        occupation_pairs = occupation_skills.get(uri, [])
        search_score = _search_match(query.strip(), occupation, occupation_pairs)
        if query.strip() and search_score is None:
            continue
        essential = [
            pair for pair in occupation_pairs
            if _value(pair[1], "relation_type").casefold() == "essential"
        ]
        optional = [
            pair for pair in occupation_pairs
            if _value(pair[1], "relation_type").casefold() == "optional"
        ]
        essential_uris = {str(skill["uri"]) for skill, _ in essential}
        optional_uris = {str(skill["uri"]) for skill, _ in optional}
        exact_uris = {skill_uri for skill_uri in exact_skill_map.values()}
        exact_essential = essential_uris & exact_uris
        exact_optional = optional_uris & exact_uris
        related_uris = {uri_value for uri_value, _ in related_skill_map.values()}
        related_uris -= exact_uris
        related_similarity = {
            uri_value: max(similarity for mapped_uri, similarity in related_skill_map.values()
                           if mapped_uri == uri_value)
            for uri_value in related_uris
        }
        essential_coverage = (
            (
                len(exact_essential)
                + sum(
                    min(0.75, similarity)
                    for uri_value, similarity in related_similarity.items()
                    if uri_value in essential_uris
                )
            ) / len(essential_uris)
            if essential_uris
            else 0.0
        )
        optional_coverage = (
            (
                len(exact_optional)
                + sum(
                    min(0.75, similarity)
                    for uri_value, similarity in related_similarity.items()
                    if uri_value in optional_uris
                )
            ) / len(optional_uris)
            if optional_uris
            else 0.0
        )
        occupation_vector = embeddings["occupation"].get(uri)
        semantic = (
            max(0.0, float(np.dot(profile_vector, occupation_vector)))
            if profile_vector is not None and occupation_vector is not None
            else 0.0
        )
        preference = _preference_similarity(
            _value(occupation, "preferred_label"),
            _labels(_value(occupation, "alt_labels", "[]")),
            interests,
        )
        if has_profile:
            weights = (0.45, 0.35, 0.10, 0.10) if interests else (0.50, 0.35, 0.15, 0.0)
            compatibility = (
                weights[0] * semantic
                + weights[1] * min(1.0, essential_coverage)
                + weights[2] * min(1.0, optional_coverage)
                + weights[3] * preference
            )
            compatibility_score: int | None = round(max(0.0, min(1.0, compatibility)) * 100)
        else:
            compatibility_score = None

        matched_names: list[str] = []
        related_names: list[str] = []
        for skill, _ in occupation_pairs:
            skill_uri = str(skill["uri"])
            for name, is_related, _ in recognized_by_uri.get(skill_uri, []):
                if is_related:
                    label = _value(skill, "preferred_label")
                    related_names.append(f"{name} (related to {label})")
                else:
                    matched_names.append(name)
        required_names = list(dict.fromkeys(
            _value(skill, "preferred_label") for skill, _ in essential
        ))
        optional_names = list(dict.fromkeys(
            _value(skill, "preferred_label") for skill, _ in optional
        ))
        missing_names = [
            _value(skill, "preferred_label")
            for skill, _ in essential
            if str(skill["uri"]) not in (exact_uris | related_uris)
        ]
        matched_names = list(dict.fromkeys(matched_names))
        related_names = list(dict.fromkeys(related_names))
        missing_names = list(dict.fromkeys(missing_names))
        title = _value(occupation, "preferred_label")
        alternate_titles = _labels(_value(occupation, "alt_labels", "[]"))
        reasons: list[str] = []
        if has_profile and semantic >= 0.35:
            reasons.append("The occupation description is semantically related to your saved profile.")
        if has_profile and essential_uris:
            reasons.append(
                f"Your profile matches {len(exact_essential) + len(related_uris & essential_uris)} "
                f"of {len(essential_uris)} essential ESCO skills."
            )
        if exact_optional or related_uris & optional_uris:
            reasons.append("Your profile also matches optional skills listed for this occupation.")
        if preference:
            reasons.append("The occupation title or an alternative title matches a saved career preference.")
        if search_score is not None and query.strip():
            reasons.append(f"The occupation matches your search for “{query.strip()}”.")
        if not reasons:
            reasons.append("Add confirmed skills or profile details to improve this estimate.")
        ranked.append({
            "id": uri,
            "title": title,
            "alternate_titles": alternate_titles,
            "description": _value(occupation, "description"),
            "category_code": _value(occupation, "isco_group"),
            "category": f"ISCO { _value(occupation, 'isco_group') }".strip(),
            "source": "ESCO",
            "compatibility_score": compatibility_score,
            "semantic_similarity": round(semantic * 100, 1) if has_profile else None,
            "essential_skill_match": round(min(1.0, essential_coverage) * 100, 1),
            "optional_skill_match": round(min(1.0, optional_coverage) * 100, 1),
            "preference_match": round(preference * 100, 1),
            "matched_skills": matched_names,
            "related_skills": related_names,
            "missing_skills": missing_names[:10],
            "required_skills": required_names[:10],
            "optional_skills": optional_names[:10],
            "matched_count": len(matched_names) + len(related_names),
            "reason": " ".join(reasons),
            "search_score": search_score or 0.0,
        })

    ranked.sort(
        key=lambda role: (
            -float(role["search_score"]) if query.strip() else 0.0,
            -(int(role["compatibility_score"]) if role["compatibility_score"] is not None else -1),
            -int(role["matched_count"]),
            str(role["title"]).casefold(),
            str(role["id"]),
        )
    )
    for role in ranked:
        role.pop("search_score", None)
    return {
        "roles": ranked[:limit],
        "considered": len(occupations),
        "result_count": len(ranked),
        "profile_skill_count": len(names),
        "query": query.strip(),
        "message": (
            "Add confirmed skills, education, experience, or career interests for recommendations."
            if not ranked and not has_profile and not query.strip()
            else "No ESCO roles matched that search. Try an occupation title, alternative title, or related skill."
            if not ranked and query.strip()
            else ""
        ),
    }
