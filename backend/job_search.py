"""Public job-search orchestration and local, explainable profile matching."""

from __future__ import annotations

import html
import json
import logging
import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from html.parser import HTMLParser
from typing import Any, Optional
from urllib.parse import urlparse, urlunparse

from onet_matching import skill_key
import public_job_providers


_LOGGER = logging.getLogger(__name__)

_ROLE_FAMILIES = (
    ("software developer", "software engineer", "application developer",
     "backend developer", "back end developer", "frontend developer",
     "front end developer", "programmer"),
    (
        "java developer",
        "java software developer",
        "java programmer",
        "java backend developer",
        "java engineer",
        "spring boot developer",
    ),
    (
        "python developer",
        "python engineer",
        "python software developer",
        "python backend developer",
        "django developer",
        "flask developer",
    ),
    (
        "data analyst",
        "junior data analyst",
        "data analyst intern",
        "reporting analyst",
        "business intelligence analyst",
        "business analyst",
        "analytics analyst",
    ),
    (
        "full stack developer",
        "junior full stack developer",
        "full stack engineer",
        "java developer",
        "spring boot developer",
        "software developer intern",
    ),
    (
        "ui ux designer",
        "ui/ux designer",
        "ux/ui designer",
        "ux designer",
        "ui designer",
        "product designer",
        "junior ui designer",
        "ux design intern",
    ),
    (
        "data scientist",
        "machine learning engineer",
        "ml engineer",
        "ai engineer",
        "applied scientist",
    ),
    (
        "qa engineer",
        "quality assurance engineer",
        "software quality assurance analyst",
        "software tester",
        "test engineer",
        "automation test engineer",
        "test automation engineer",
    ),
    (
        "devops engineer",
        "site reliability engineer",
        "sre",
        "platform engineer",
        "cloud engineer",
    ),
    (
        "web developer",
        "web designer",
        "frontend developer",
        "front end developer",
        "full stack developer",
    ),
    (
        "cybersecurity analyst",
        "information security analyst",
        "security engineer",
        "security analyst",
    ),
    (
        "product manager",
        "technical product manager",
        "product owner",
    ),
)

_SKILL_TERMS = {
    "api design": ("api design", "rest api", "restful api"),
    "bug tracking": ("bug tracking", "jira", "atlassian jira"),
    "data visualization": ("data visualization", "tableau", "power bi", "powerbi"),
    "excel": ("excel", "microsoft excel"),
    "javascript": ("javascript", "js", "node.js"),
    "sql": ("sql", "sql server", "postgresql", "postgres", "mysql", "sqlite"),
    "test automation": ("test automation", "selenium", "pytest"),
}

_EXPERIENCE_TERMS = {
    "internship": ("intern", "internship", "trainee"),
    "entry": ("entry level", "entry-level", "junior", "graduate", "fresher", "0-2 years"),
    "mid": ("mid level", "mid-level", "3+ years", "experienced"),
    "senior": ("senior", "lead", "principal", "staff", "8+ years"),
}

_GENERIC_TITLE_WORDS = {
    "and", "at", "for", "in", "of", "the", "a", "an",
    "senior", "junior", "lead", "principal", "staff", "intern",
    "internship", "graduate", "entry", "level", "remote", "hybrid",
}


JobSearchProviderError = public_job_providers.ProviderError

class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _normalize_title(value: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", value.casefold()).strip()


def related_role_terms(title: str) -> list[str]:
    normalized = _normalize_title(title)
    if not normalized:
        return []
    for family in _ROLE_FAMILIES:
        normalized_family = {_normalize_title(item) for item in family}
        if normalized in normalized_family:
            return list(family)
    return [title.strip()]


def generate_search_queries(
    target_titles: list[str],
    experience_level: str,
    *,
    limit: int = 8,
) -> list[str]:
    """Create separate provider searches for selected roles and their close variants."""
    role_queries: list[list[str]] = []
    for title in target_titles:
        candidates = [title.strip(), *related_role_terms(title)]
        if experience_level == "internship":
            candidates.insert(1, f"{title} Intern")
        elif experience_level == "entry":
            candidates.append(f"{title} fresher")
        role_queries.append(candidates)

    queries: list[str] = []
    seen: set[str] = set()
    for variant_index in range(max((len(variants) for variants in role_queries), default=0)):
        for variants in role_queries:
            if variant_index >= len(variants):
                continue
            query = variants[variant_index].strip()
            key = _normalize_title(query)
            if key and key not in seen:
                queries.append(query)
                seen.add(key)
            if len(queries) >= limit:
                return queries
    return queries


def infer_profile_roles(skill_names: list[str]) -> list[str]:
    keys = {skill_key(name) for name in skill_names}
    roles: list[str] = []
    if keys & {"figma", "wireframing", "prototyping", "user experience design"}:
        roles.append("UI/UX Designer")
    if {"java", "react"} <= keys or "spring boot" in keys:
        roles.extend(("Full Stack Developer", "Java Developer"))
    if {"python", "sql"} <= keys or (
        "excel" in keys and keys & {"power bi", "tableau", "data visualization"}
    ):
        roles.append("Data Analyst")
    if keys & {"react", "javascript"} and not roles:
        roles.append("Frontend Developer")
    return list(dict.fromkeys(roles))


def profile_experience_level(work_experience: str) -> str:
    text = work_experience.casefold()
    if any(term in text for term in ("fresher", "no experience", "entry level", "internship")):
        return "entry"
    years = re.search(r"\b(\d{1,2})\+?\s*(?:years?|yrs?)\b", text)
    if years:
        count = int(years.group(1))
        return "entry" if count <= 2 else "mid" if count <= 7 else "senior"
    return ""


def _clean_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    parser = _TextExtractor()
    parser.feed(value)
    return re.sub(r"\s+", " ", html.unescape(" ".join(parser.parts))).strip()


def _safe_job_url(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value.strip())
    hostname = (parsed.hostname or "").casefold()
    if (
        parsed.scheme != "https"
        or not hostname or parsed.username or parsed.password
    ):
        return None
    return value.strip()


def _dedupe_url(value: str) -> str:
    parsed = urlparse(value)
    return urlunparse((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path, "", "", ""))


def _number(value: object) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _normalize_job(item: object) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    title = _clean_text(item.get("title"))
    application_url = _safe_job_url(item.get("application_url"))
    identifier = str(item.get("id") or "").strip()
    if not title or not application_url or not identifier:
        return None

    return {
        "id": identifier, "title": title,
        "company": _clean_text(item.get("company")),
        "location": _clean_text(item.get("location")),
        "description": _clean_text(item.get("description")),
        "salary_text": _clean_text(item.get("salary_text")),
        "salary_min": _number(item.get("salary_min")),
        "salary_max": _number(item.get("salary_max")),
        "salary_currency": _clean_text(item.get("salary_currency")),
        "salary_period": _clean_text(item.get("salary_period")),
        "salary_is_predicted": bool(item.get("salary_is_predicted", False)),
        "created": _clean_text(item.get("created")),
        "employment_type": _clean_text(item.get("employment_type")),
        "employment_type_label": _clean_text(item.get("employment_type_label")),
        "contract_time": _clean_text(item.get("contract_time")),
        "category": _clean_text(item.get("category")),
        "work_mode": _clean_text(item.get("work_mode")),
        "application_url": application_url,
        "source": _clean_text(item.get("source")),
        "source_url": _safe_job_url(item.get("source_url")) or "",
        "experience_levels": item.get("experience_levels", []),
        "location_eligible": item.get("location_eligible"),
    }


def _salary_range(text):
    # Do not assume a salary period or convert foreign currency silently.
    if not re.search(r"INR|₹|Rs\.?", text, re.I):
        return None, None
    factor = 12 if re.search(r"month|monthly|/mo\b", text, re.I) else 1 if re.search(r"year|annual|annum|/yr\b|lpa", text, re.I) else None
    if factor is None:
        return None, None
    values = re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", text)
    if not 1 <= len(values) <= 2:
        return None, None
    multiplier = 100000 if re.search(r"lakh|lac|lpa", text, re.I) else 1000 if re.search(r"\d\s*k\b", text, re.I) else 1
    numbers = [float(v.replace(',', '')) * multiplier * factor for v in values]
    return min(numbers), max(numbers)


def _development_log(event: str, **details: object) -> None:
    if (
        os.environ.get("APP_ENV", "").casefold() == "development"
        or os.environ.get("DEBUG", "").casefold() == "true"
    ):
        _LOGGER.info("job-search %s %s", event, json.dumps(details, sort_keys=True))


async def search_listings(
    *,
    search_queries: list[str],
    target_titles: list[str],
    skill_names: list[str],
    cache_path,
):
    payload = await public_job_providers.fetch_public_listings(search_queries, cache_path)
    jobs, seen_ids, seen_urls = [], set(), set()
    for item in payload["jobs"]:
        job = _normalize_job(item)
        if not job:
            continue
        url = _dedupe_url(job["application_url"])
        identity = (
            _normalize_title(job["title"]),
            _normalize_title(job["company"]),
            _normalize_title(job["location"]),
        )
        if job["id"] in seen_ids or url in seen_urls or identity in seen_ids:
            continue
        seen_ids.add(job["id"])
        seen_urls.add(url)
        seen_ids.add(identity)
        jobs.append(job)

    relevant = []
    for job in jobs:
        text = f"{job['title']} {job['description']}"
        title_score, _ = title_match_score(job["title"], target_titles)
        matched_skills = extract_job_skills(text, skill_names)
        normalized_queries = [_normalize_title(term) for term in search_queries]
        direct_query = any(
            query and query in _normalize_title(job["title"])
            for query in normalized_queries
        )
        if title_score >= 50 or len(matched_skills) >= 3 or direct_query:
            relevant.append(job)

    cached = payload["cache_hits"] == payload["cache_total"]
    return {
        "total": len(relevant),
        "jobs": relevant,
        "failed_queries": payload["provider_warnings"],
        "providers": payload["providers"],
        "search_queries": search_queries,
    }, cached


def _skill_terms(name: str) -> tuple[str, ...]:
    key = skill_key(name)
    return _SKILL_TERMS.get(key, (name,))


@lru_cache(maxsize=4096)
def _term_pattern(term: str) -> Optional[re.Pattern[str]]:
    normalized = term.strip()
    if len(normalized) < 3 and normalized.casefold() not in {"js", "c++", "c#"}:
        return None
    escaped = re.escape(normalized).replace(r"\ ", r"\s+")
    return re.compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", re.IGNORECASE)


def extract_job_skills(text: str, skill_names: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for name in skill_names:
        key = skill_key(name)
        if not key:
            continue
        for term in _skill_terms(name):
            pattern = _term_pattern(term)
            if pattern and pattern.search(text):
                found.setdefault(key, name)
                break
    return found


def infer_experience_level(job: dict[str, Any]) -> str:
    provided_levels = job.get("experience_levels")
    if isinstance(provided_levels, list):
        labels = {str(value).casefold() for value in provided_levels}
        has_internship = any("intern" in value or "trainee" in value for value in labels)
        has_entry = any("entry" in value or "junior" in value or "graduate" in value for value in labels)
        has_senior = any(
            term in value for value in labels for term in ("senior", "lead", "principal", "manager", "staff")
        )
        has_mid = any("mid" in value for value in labels)
        if has_internship and not (has_entry or has_senior or has_mid):
            return "internship"
        if has_entry and not (has_senior or has_mid):
            return "entry"
        if has_senior and not (has_entry or has_internship):
            return "senior"
        if has_mid and not (has_entry or has_senior or has_internship):
            return "mid"

    title = str(job.get("title", "")).casefold()
    description = str(job.get("description", "")).casefold()
    ranked_title = re.search(
        r"\b(?:engineer|developer|scientist|analyst|architect|sde)\s*[- ]?\s*(iii|iv|3|4)\b"
        r"|\blevel\s*(iii|iv|3|4)\b",
        title,
    )
    if ranked_title:
        rank = next((value for value in ranked_title.groups() if value), "")
        return "senior" if rank in {"iv", "4"} else "mid"

    def detected(text: str) -> set[str]:
        levels = {
            level for level in ("internship", "senior", "entry", "mid")
            if any(term in text for term in _EXPERIENCE_TERMS[level])
        }
        years = re.search(r"\b(\d{1,2})\+?\s*(?:years?|yrs?)\b", text)
        if years:
            count = int(years.group(1))
            levels.add("entry" if count <= 2 else "mid" if count <= 7 else "senior")
        return levels

    title_levels = detected(title)
    description_levels = detected(description)
    if len(title_levels) > 1 or len(description_levels) > 1:
        return ""
    if title_levels and description_levels and title_levels != description_levels:
        return ""
    levels = title_levels or description_levels
    return next(iter(levels)) if levels else ""


def _experience_compatible(expected: str, observed: str) -> bool:
    if expected == "entry" and observed == "internship":
        return True
    return expected == observed


def _location_matches(preferred: str, listing: str) -> Optional[bool]:
    wanted = _normalize_title(preferred)
    found = _normalize_title(listing)
    if not wanted or not found:
        return None
    aliases = {"bangalore": "bengaluru", "bombay": "mumbai", "madras": "chennai"}
    wanted = " ".join(aliases.get(token, token) for token in wanted.split())
    found = " ".join(aliases.get(token, token) for token in found.split())
    if wanted in found or found in wanted:
        return True
    if wanted == "india":
        return "india" in found
    return False


def _category_matches(preferred: str, listing: str) -> Optional[bool]:
    if not preferred or not listing:
        return None
    wanted = _normalize_title(preferred.replace("-", " "))
    wanted = re.sub(r"\bjobs?\b", "", wanted).strip()
    found = _normalize_title(listing)
    found = re.sub(r"\bjobs?\b", "", found).strip()
    return wanted == found or wanted in found or found in wanted


def filter_jobs(
    jobs: list[dict[str, Any]],
    *,
    experience_level: str,
    location: str,
    employment_type: str,
    minimum_salary: Optional[int],
    career_category: str,
    work_mode: str,
    allow_senior: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    filtered: list[dict[str, Any]] = []
    for job in jobs:
        if minimum_salary is not None:
            maximum = job.get("salary_max")
            minimum = job.get("salary_min")
            if maximum is not None and maximum < minimum_salary:
                continue
            if minimum is not None and maximum is not None and maximum < minimum_salary:
                continue
        location_match = _location_matches(location, str(job.get("location") or ""))
        india_eligible = job.get("location_eligible") is True
        preferred_is_india = _normalize_title(location) == "india"
        if location:
            if job.get("location_eligible") is False:
                continue
            if location_match is False:
                if preferred_is_india and india_eligible:
                    pass
                elif job.get("work_mode") == "remote" and india_eligible:
                    pass
                else:
                    continue
            elif location_match is None and not india_eligible:
                continue
        if career_category and _category_matches(
            career_category, str(job.get("category") or "")
        ) is False:
            continue
        if work_mode != "any" and job.get("work_mode") and job["work_mode"] != work_mode:
            continue

        observed_experience = infer_experience_level(job)
        if experience_level == "entry" and not allow_senior:
            provided_levels = job.get("experience_levels")
            has_entry_level = isinstance(provided_levels, list) and any(
                any(term in str(value).casefold() for term in ("entry", "junior", "intern", "graduate"))
                for value in provided_levels
            )
            has_senior_level = isinstance(provided_levels, list) and any(
                any(term in str(value).casefold() for term in ("senior", "lead", "principal", "staff", "manager"))
                for value in provided_levels
            )
            senior_title = bool(re.search(
                r"\b(senior|lead|principal|staff|manager|director|head)\b",
                str(job.get("title", "")),
                re.I,
            ))
            if has_senior_level and not has_entry_level or senior_title:
                continue
        if (
            experience_level != "any"
            and observed_experience
            and not _experience_compatible(experience_level, observed_experience)
        ):
            continue
        if employment_type != "any":
            contract_type = str(job.get("employment_type") or "").casefold()
            contract_time = str(job.get("contract_time") or "").casefold()
            if employment_type == "internship":
                if observed_experience and observed_experience != "internship":
                    continue
            elif employment_type in {"full_time", "part_time"}:
                observed = contract_time or ""
                if observed and observed != employment_type:
                    continue
            elif contract_type and contract_type != employment_type:
                continue
        filtered.append(job)
    return filtered, len(jobs) - len(filtered)


def _title_tokens(title: str) -> set[str]:
    return {
        token for token in _normalize_title(title).split()
        if token not in _GENERIC_TITLE_WORDS
    }


def title_match_score(job_title: str, target_titles: list[str]) -> tuple[int, str]:
    job_normalized = _normalize_title(job_title)
    job_tokens = _title_tokens(job_title)
    best = 0
    best_target = ""
    for target in target_titles:
        for related in related_role_terms(target):
            target_normalized = _normalize_title(related)
            target_tokens = _title_tokens(related)
            if not target_tokens:
                continue
            if job_normalized == target_normalized:
                score = 100
            elif target_tokens.issubset(job_tokens):
                score = 95 if related.casefold() == target.casefold() else 88
            else:
                overlap = len(target_tokens & job_tokens) / len(target_tokens)
                ratio = SequenceMatcher(None, target_normalized, job_normalized).ratio()
                score = round(max(overlap * 75, ratio * 60))
                if score < 50:
                    score = 0
            if score > best:
                best = score
                best_target = target
    return best, best_target


def score_job(
    job: dict[str, Any],
    *,
    target_titles: list[str],
    profile_skills: dict[str, dict[str, Any]],
    skill_catalog: list[str],
    education: str,
    work_experience: str,
    experience_level: str,
    location: str,
    employment_type: str,
    minimum_salary: Optional[int],
    career_category: str,
    work_mode: str,
) -> dict[str, Any]:
    searchable_text = f"{job['title']} {job['description']}"
    detected = extract_job_skills(searchable_text, skill_catalog)
    detected.update({
        key: str(item["name"])
        for key, item in profile_skills.items()
        if any(pattern and pattern.search(searchable_text) for term in _skill_terms(str(item["name"]))
               for pattern in [_term_pattern(term)])
    })
    matched = sorted(
        (name for key, name in detected.items() if key in profile_skills),
        key=str.casefold,
    )
    missing = sorted(
        (name for key, name in detected.items() if key not in profile_skills),
        key=str.casefold,
    )
    title_score, matched_target = title_match_score(job["title"], target_titles)
    skill_score = (
        round(len(matched) / (len(profile_skills) + len(missing)) * 100)
        if detected and profile_skills
        else None
    )
    expected_experience = experience_level
    if expected_experience == "any":
        expected_experience = infer_experience_level({"title": "", "description": work_experience})
    observed_experience = infer_experience_level(job)
    experience_score = None
    if expected_experience and observed_experience:
        experience_score = (
            100 if _experience_compatible(expected_experience, observed_experience) else 0
        )

    location_match = _location_matches(location, str(job.get("location") or ""))
    location_score = (
        100 if job.get("location") else None
    ) if location.casefold() == "india" else (
        None if location_match is None else 100 if location_match else 0
    )

    other_scores: list[int] = []
    if employment_type != "any":
        contract_type = str(job.get("employment_type") or "").casefold()
        contract_time = str(job.get("contract_time") or "").casefold()
        observed_type = observed_experience if employment_type == "internship" else (
            contract_time if employment_type in {"full_time", "part_time"} else contract_type
        )
        if observed_type:
            other_scores.append(100 if observed_type == employment_type else 0)
    if work_mode != "any" and job.get("work_mode"):
        other_scores.append(100 if job["work_mode"] == work_mode else 0)
    if career_category:
        category_match = _category_matches(career_category, str(job.get("category") or ""))
        if category_match is not None:
            other_scores.append(100 if category_match else 0)
    if minimum_salary is not None:
        salary_min = job.get("salary_min")
        salary_max = job.get("salary_max")
        if salary_min is not None or salary_max is not None:
            other_scores.append(
                100 if (salary_max if salary_min is None else salary_min) >= minimum_salary else 0
            )
    if education:
        education_tokens = _title_tokens(education)
        job_tokens = _title_tokens(searchable_text)
        if education_tokens & job_tokens:
            other_scores.append(100)
    other_score = round(sum(other_scores) / len(other_scores)) if other_scores else None

    components: dict[str, Optional[int]] = {
        "preferred_role": title_score if target_titles else None,
        "technical_skills": skill_score,
        "experience": experience_score,
        "location": location_score,
        "other_preferences": other_score,
    }
    weights = {
        "preferred_role": 35,
        "technical_skills": 35,
        "experience": 15,
        "location": 10,
        "other_preferences": 5,
    }
    available = [
        (weights[name], value)
        for name, value in components.items()
        if value is not None
    ]
    compatibility = (
        round(sum(weight * value for weight, value in available) / sum(weight for weight, _ in available))
        if available
        else None
    )
    score_coverage = sum(weight for weight, _ in available)

    reasons: list[str] = []
    if title_score >= 80 and matched_target:
        relation = "related" if job_title_is_alternative(job["title"], matched_target) else "direct"
        reasons.append(f"{job['title']} is a {relation} title for {matched_target}.")
    elif title_score:
        reasons.append(f"The job title has some overlap with your selected role.")
    if matched:
        reasons.append(
            f"The listing mentions {len(matched)} of your profile skills: {', '.join(matched[:5])}."
        )
    elif profile_skills:
        reasons.append("No saved profile skill was explicitly found in the listing text.")
    else:
        reasons.append("Add profile skills to improve the skill compatibility estimate.")
    if missing:
        reasons.append(
            "The listing also mentions skills not in your profile (these are text matches, not verified requirements)."
        )
    if expected_experience:
        reasons.append(f"Experience level compared: {expected_experience}.")
    if location:
        reasons.append(f"Live search was restricted to {location}.")
    if education or work_experience:
        reasons.append("Education and experience information informed local matching.")

    unknown_match_data = [
        label for key, label in (
            ("technical_skills", "job skills"),
            ("experience", "job experience level"),
            ("location", "job location"),
            ("other_preferences", "job preference details"),
        )
        if components[key] is None
    ]

    return {
        **job,
        "compatibility_score": compatibility,
        "score_coverage": score_coverage,
        "skill_match_percentage": skill_score,
        "score_components": components,
        "unknown_match_data": unknown_match_data,
        "matched_skills": matched,
        "missing_skills": missing,
        "reasons": reasons,
        "is_alternative": bool(
            target_titles and matched_target and job_title_is_alternative(job["title"], matched_target)
        ),
        "matched_preference": matched_target,
    }


def job_title_is_alternative(job_title: str, target: str) -> bool:
    job_normalized = _normalize_title(job_title)
    target_normalized = _normalize_title(target)
    if job_normalized == target_normalized:
        return False
    target_tokens = _title_tokens(target)
    job_tokens = _title_tokens(job_title)
    return not target_tokens.issubset(job_tokens)


def sort_ranked_jobs(jobs: list[dict[str, Any]], sort_by: str) -> None:
    def compatibility(job: dict[str, Any]) -> int:
        score = job.get("compatibility_score")
        return int(score) if score is not None else -1

    if sort_by == "date":
        jobs.sort(
            key=lambda job: (
                str(job.get("created", "")),
                job.get("compatibility_score") is not None,
                compatibility(job),
            ),
            reverse=True,
        )
    elif sort_by in {"salary_asc", "salary_desc"}:
        reverse = sort_by == "salary_desc"
        jobs.sort(
            key=lambda job: (
                job.get("salary_min") is None,
                -(job.get("salary_min") or 0) if reverse else (job.get("salary_min") or 0),
                -compatibility(job),
            ),
        )
    else:
        jobs.sort(
            key=lambda job: (
                -compatibility(job),
                str(job.get("created", "")),
                str(job.get("title", "")).casefold(),
            )
        )

