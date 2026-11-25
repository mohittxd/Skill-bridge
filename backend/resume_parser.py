"""Small, local-only resume parser used by the skill-gap demo.

The parser intentionally uses readable heuristics rather than pretending to be
an AI resume evaluator.  Files are parsed from bytes in memory, skill names are
matched against the project vocabulary, and no proficiency level is guessed.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Optional


MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_PDF_PAGES = 50
MAX_EXTRACTED_CHARS = 500_000
MAX_ZIP_ENTRIES = 1_000
MAX_ZIP_ENTRY_SIZE = 10 * 1024 * 1024
MAX_ZIP_UNCOMPRESSED_SIZE = 25 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200

# Keys are common resume terms; values are the canonical skill returned by the
# API.  Java is deliberately not an alias for JavaScript.
SKILL_ALIASES: dict[str, str] = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "rest api": "API Design",
    "rest apis": "API Design",
    "restful api": "API Design",
    "restful apis": "API Design",
    "postgres": "SQL",
    "postgresql": "SQL",
    "mysql": "SQL",
    "power bi": "Data Visualization",
    "powerbi": "Data Visualization",
    "tableau": "Data Visualization",
    "microsoft excel": "Excel",
    "ms excel": "Excel",
    "github": "Git",
    "selenium": "Test Automation",
    "pytest": "Test Automation",
    "manual testing": "Testing Fundamentals",
    "jira": "Bug Tracking",
}


_EMAIL_RE = re.compile(
    r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+",
    re.IGNORECASE,
)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d().\s-]{7,}\d)(?!\w)")

_SECTION_NAMES = {
    "summary": "summary",
    "professional summary": "summary",
    "profile": "summary",
    "about me": "summary",
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
    "skills and tools": "skills",
    "technologies": "skills",
    "education": "education",
    "academic background": "education",
    "academic qualifications": "education",
    "qualifications": "education",
    "work experience": "work_experience",
    "professional experience": "work_experience",
    "employment history": "work_experience",
    "employment": "work_experience",
    "experience": "work_experience",
    "projects": "other",
    "certifications": "other",
    "certification": "other",
    "achievements": "other",
    "languages": "other",
    "interests": "other",
    "contact": "other",
    "contact details": "other",
}


def _compact_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _alias_key(value: str) -> str:
    value = value.casefold().strip()
    value = re.sub(r"[\s/_-]+", " ", value)
    return value


def normalize_skill(name: str, known_skills: list[str]) -> Optional[str]:
    """Return a canonical known/aliased skill, or ``None`` if unsupported."""

    cleaned = _compact_spaces(name)
    if not cleaned:
        return None

    known_by_key = {
        _alias_key(skill): _compact_spaces(skill)
        for skill in known_skills
        if _compact_spaces(skill)
    }
    direct_match = known_by_key.get(_alias_key(cleaned))
    if direct_match:
        return direct_match

    alias_match = SKILL_ALIASES.get(_alias_key(cleaned))
    if not alias_match:
        return None
    return known_by_key.get(_alias_key(alias_match), alias_match)


def _validate_input(data: bytes, filename: str) -> str:
    if not isinstance(data, bytes):
        raise ValueError("Resume data must be provided as bytes.")
    if not data:
        raise ValueError("The uploaded resume is empty.")
    if len(data) > MAX_FILE_SIZE:
        raise ValueError("Resume files must be 5 MiB or smaller.")

    extension = Path(filename or "").suffix.casefold()
    if extension not in {".pdf", ".docx"}:
        raise ValueError("Only PDF and DOCX resume files are supported.")

    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise ValueError("File content does not match the .pdf extension.")
    if extension == ".docx" and not data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
        raise ValueError("File content does not match the .docx extension.")
    return extension


def _extract_pdf_text(data: bytes) -> tuple[str, list[str]]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment/setup problem
        raise RuntimeError("PDF support is not installed.") from exc

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception as exc:
        raise ValueError("The PDF is corrupt or could not be read.") from exc

    if reader.is_encrypted:
        raise ValueError("Encrypted PDF resumes are not supported.")

    try:
        page_count = len(reader.pages)
    except Exception as exc:
        raise ValueError("The PDF is corrupt or could not be read.") from exc
    if page_count > MAX_PDF_PAGES:
        raise ValueError(f"PDF resumes may contain at most {MAX_PDF_PAGES} pages.")

    parts: list[str] = []
    blank_pages = 0
    extracted_length = 0
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:
            raise ValueError("The PDF is corrupt or contains an unreadable page.") from exc
        if not page_text.strip():
            blank_pages += 1
            continue
        extracted_length += len(page_text)
        if extracted_length > MAX_EXTRACTED_CHARS:
            raise ValueError("The resume contains too much extracted text to process safely.")
        parts.append(page_text)

    text = "\n".join(parts)
    if not text.strip():
        raise ValueError(
            "No extractable text was found. Scanned resumes need OCR before upload."
        )

    warnings: list[str] = []
    if blank_pages:
        warnings.append(
            f"{blank_pages} PDF page{'s' if blank_pages != 1 else ''} contained no extractable text."
        )
    return text, warnings


def _validate_docx_archive(data: bytes) -> None:
    buffer = io.BytesIO(data)
    if not zipfile.is_zipfile(buffer):
        raise ValueError("The DOCX file is corrupt or is not a valid Office document.")

    buffer.seek(0)
    try:
        with zipfile.ZipFile(buffer) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ZIP_ENTRIES:
                raise ValueError("The DOCX file contains too many internal files.")

            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise ValueError("The file is not a valid DOCX resume.")

            total_size = 0
            for entry in entries:
                path_parts = Path(entry.filename.replace("\\", "/")).parts
                if entry.filename.startswith(("/", "\\")) or ".." in path_parts:
                    raise ValueError("The DOCX file contains an unsafe internal path.")
                if entry.flag_bits & 0x1:
                    raise ValueError("Encrypted DOCX resumes are not supported.")
                if entry.file_size > MAX_ZIP_ENTRY_SIZE:
                    raise ValueError("The DOCX file contains an oversized internal file.")
                total_size += entry.file_size
                if total_size > MAX_ZIP_UNCOMPRESSED_SIZE:
                    raise ValueError("The DOCX file expands beyond the safe processing limit.")
                if entry.file_size and (
                    entry.compress_size == 0
                    or entry.file_size / entry.compress_size > MAX_ZIP_COMPRESSION_RATIO
                ):
                    raise ValueError("The DOCX file has an unsafe compression ratio.")

            if archive.testzip() is not None:
                raise ValueError("The DOCX file is corrupt or failed its integrity check.")
    except ValueError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise ValueError("The DOCX file is corrupt or could not be read.") from exc


def _extract_docx_text(data: bytes) -> tuple[str, list[str]]:
    _validate_docx_archive(data)
    try:
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover - deployment/setup problem
        raise RuntimeError("DOCX support is not installed.") from exc

    try:
        document = Document(io.BytesIO(data))
        parts: list[str] = []
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph) and block.text.strip():
                parts.append(block.text)
            elif isinstance(block, Table):
                for row in block.rows:
                    cell_values = [_compact_spaces(cell.text) for cell in row.cells]
                    row_text = " | ".join(value for value in cell_values if value)
                    if row_text:
                        parts.append(row_text)
    except Exception as exc:
        raise ValueError("The DOCX file is corrupt or could not be read.") from exc

    text = "\n".join(parts)
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ValueError("The resume contains too much extracted text to process safely.")
    if not text.strip():
        raise ValueError("No extractable text was found in the DOCX resume.")
    return text, []


def _clean_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.replace("\r", "\n").replace("\xa0", " ").split("\n"):
        cleaned = re.sub(r"^[\s•●▪◦‣*–—-]+", "", raw_line)
        cleaned = _compact_spaces(cleaned)
        if cleaned:
            lines.append(cleaned)
    return lines


def _looks_like_name_word(word: str) -> bool:
    letters_only = word.strip(".'-’")
    return bool(letters_only) and letters_only.replace("-", "").replace("'", "").isalpha()


def _extract_name(lines: list[str]) -> str:
    title_words = {
        "resume",
        "curriculum",
        "vitae",
        "profile",
        "summary",
        "skills",
        "education",
        "experience",
        "engineer",
        "developer",
        "analyst",
        "manager",
        "specialist",
    }
    for line in lines[:10]:
        candidate = line.strip(" |,:;")
        if not 3 <= len(candidate) <= 70:
            continue
        if _EMAIL_RE.search(candidate) or _PHONE_RE.search(candidate):
            continue
        if "http" in candidate.casefold() or "www." in candidate.casefold() or "|" in candidate:
            continue
        words = candidate.split()
        if not 2 <= len(words) <= 5 or not all(_looks_like_name_word(word) for word in words):
            continue
        normalized_words = {word.strip(".'-’").casefold() for word in words}
        if normalized_words & title_words:
            continue
        if not all(word[:1].isupper() for word in words):
            continue
        return candidate
    return ""


def _extract_email(text: str) -> str:
    match = _EMAIL_RE.search(text)
    return match.group(0) if match else ""


def _extract_phone(text: str) -> str:
    for match in _PHONE_RE.finditer(text):
        value = _compact_spaces(match.group(0))
        digit_count = sum(character.isdigit() for character in value)
        if 10 <= digit_count <= 15:
            return value
    return ""


def _heading_details(line: str) -> tuple[Optional[str], str]:
    heading, separator, inline_value = line.partition(":")
    normalized = re.sub(r"\s+", " ", heading.casefold().replace("&", "and")).strip()
    section = _SECTION_NAMES.get(normalized)
    if section:
        return section, inline_value.strip() if separator else ""
    return None, ""


def _extract_section(lines: list[str], target: str) -> str:
    collected: list[str] = []
    active = False
    for line in lines:
        section, inline_value = _heading_details(line)
        if section is not None:
            if active and section != target:
                break
            if section == target:
                active = True
                if inline_value:
                    collected.append(inline_value)
            continue
        if active:
            collected.append(line)
    return "\n".join(collected).strip()


def _education_fallback(lines: list[str]) -> str:
    pattern = re.compile(
        r"\b(?:bachelor|master|b\.?sc|m\.?sc|b\.?tech|m\.?tech|mba|degree|diploma|"
        r"university|college)\b",
        re.IGNORECASE,
    )
    matches = [line for line in lines if pattern.search(line)]
    return "\n".join(matches[:4])


def _experience_fallback(lines: list[str]) -> str:
    role_pattern = re.compile(
        r"\b(?:intern|engineer|developer|analyst|tester|associate|manager|specialist)\b",
        re.IGNORECASE,
    )
    date_pattern = re.compile(r"\b(?:19|20)\d{2}\b|\bpresent\b", re.IGNORECASE)
    matches = [line for line in lines if role_pattern.search(line) and date_pattern.search(line)]
    return "\n".join(matches[:6])


def _skill_pattern(term: str) -> re.Pattern[str]:
    escaped = re.escape(term.strip())
    escaped = escaped.replace(r"\ ", r"[\s/&-]+")
    return re.compile(
        rf"(?<![A-Za-z0-9+#]){escaped}(?![A-Za-z0-9+#])",
        re.IGNORECASE,
    )


def _find_skills(text: str, known_skills: list[str]) -> list[dict[str, object]]:
    candidates: list[tuple[int, int, str]] = []
    known_by_key: dict[str, str] = {}
    for skill in known_skills:
        cleaned = _compact_spaces(skill)
        if cleaned:
            known_by_key.setdefault(_alias_key(cleaned), cleaned)

    for canonical in known_by_key.values():
        for match in _skill_pattern(canonical).finditer(text):
            candidates.append((match.start(), 0, canonical))

    for alias, alias_canonical in SKILL_ALIASES.items():
        canonical = known_by_key.get(_alias_key(alias_canonical), alias_canonical)
        for match in _skill_pattern(alias).finditer(text):
            candidates.append((match.start(), 1, canonical))

    skills: list[dict[str, object]] = []
    seen: set[str] = set()
    for _, _, canonical in sorted(candidates, key=lambda item: (item[0], item[1], item[2])):
        key = canonical.casefold()
        if key in seen:
            continue
        seen.add(key)
        skills.append({"name": canonical, "level": None, "source": "resume"})
    return skills


def _profile_warnings(profile: dict[str, object]) -> list[str]:
    labels = (
        ("full_name", "A full name was not detected."),
        ("email", "An email address was not detected."),
        ("phone", "A phone number was not detected."),
        ("education", "An education section was not detected."),
        ("work_experience", "A work experience section was not detected."),
        ("skills", "No known skills were detected."),
    )
    return [message for key, message in labels if not profile.get(key)]


def parse_resume(data: bytes, filename: str, known_skills: list[str]) -> dict[str, object]:
    """Parse a PDF/DOCX resume entirely in memory.

    The returned skill levels are always ``None``.  A resume mention is useful
    as a suggestion, not evidence of proficiency.
    """

    extension = _validate_input(data, filename)
    if extension == ".pdf":
        text, warnings = _extract_pdf_text(data)
    else:
        text, warnings = _extract_docx_text(data)

    lines = _clean_lines(text)
    education = _extract_section(lines, "education") or _education_fallback(lines)
    work_experience = (
        _extract_section(lines, "work_experience") or _experience_fallback(lines)
    )
    profile: dict[str, object] = {
        "full_name": _extract_name(lines),
        "email": _extract_email(text),
        "phone": _extract_phone(text),
        "education": education,
        "work_experience": work_experience,
        "skills": _find_skills(text, known_skills),
    }
    warnings.extend(_profile_warnings(profile))
    return {"profile": profile, "warnings": list(dict.fromkeys(warnings))}
