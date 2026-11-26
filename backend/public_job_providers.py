"""Credential-free clients for public job feeds and company job boards."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx


HIMALAYAS_SEARCH_URL = "https://himalayas.app/jobs/api/search"
GREENHOUSE_BOARD_URL = "https://boards-api.greenhouse.io/v1/boards"
LEVER_POSTINGS_URL = "https://api.lever.co/v0/postings"
DEFAULT_GREENHOUSE_BOARDS = ("stripe", "figma", "gitlab", "cloudflare", "databricks")
DEFAULT_LEVER_SITES = ("mindtickle", "meesho", "cred")
HIMALAYAS_PAGES_PER_QUERY = 2
LEVER_PAGE_SIZE = 100
LEVER_MAX_PAGES = 2
CACHE_SECONDS = 900
CLAIM_SECONDS = 60
MAX_CONCURRENT_REQUESTS = 6
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_INDIA_LOCATIONS = (
    "india", "bengaluru", "bangalore", "mumbai", "pune", "hyderabad",
    "chennai", "delhi", "gurugram", "gurgaon", "noida", "kolkata",
    "ahmedabad", "kochi", "thiruvananthapuram", "jaipur",
)


def _development_log(message: str, *args: object) -> None:
    if (
        os.environ.get("APP_ENV", "").casefold() == "development"
        or os.environ.get("DEBUG", "").casefold() == "true"
    ):
        _LOGGER.info(message, *args)


class ProviderError(Exception):
    def __init__(self, status_code: int, message: str, provider: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider


def _configured_names(name: str, defaults: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return defaults
    names = []
    seen = set()
    for candidate in raw.split(","):
        value = candidate.strip()
        key = value.casefold()
        if value and _SLUG.fullmatch(value) and key not in seen:
            names.append(value)
            seen.add(key)
    return tuple(names)


def greenhouse_boards() -> tuple[str, ...]:
    return _configured_names("GREENHOUSE_BOARD_TOKENS", DEFAULT_GREENHOUSE_BOARDS)


def lever_sites() -> tuple[str, ...]:
    return _configured_names("LEVER_SITE_NAMES", DEFAULT_LEVER_SITES)


def _connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_job_response_cache (
            cache_key TEXT PRIMARY KEY,
            expires_at REAL NOT NULL,
            response_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS public_job_request_claims (
            cache_key TEXT PRIMARY KEY,
            expires_at REAL NOT NULL
        )
        """
    )
    return connection


def _claim(path: Path, cache_key: str) -> tuple[str, Any]:
    now = time.time()
    with _connection(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        cached = connection.execute(
            "SELECT response_json FROM public_job_response_cache "
            "WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        ).fetchone()
        if cached:
            return "cached", json.loads(cached[0])
        active = connection.execute(
            "SELECT 1 FROM public_job_request_claims "
            "WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        ).fetchone()
        if active:
            return "wait", None
        connection.execute(
            "DELETE FROM public_job_response_cache WHERE expires_at <= ?", (now,)
        )
        connection.execute(
            "DELETE FROM public_job_request_claims WHERE expires_at <= ?", (now,)
        )
        connection.execute(
            "INSERT OR REPLACE INTO public_job_request_claims VALUES (?, ?)",
            (cache_key, now + CLAIM_SECONDS),
        )
    return "owner", None


def _store(path: Path, cache_key: str, payload: Any) -> None:
    with _connection(path) as connection:
        connection.execute(
            """
            INSERT INTO public_job_response_cache(cache_key, expires_at, response_json)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                expires_at = excluded.expires_at,
                response_json = excluded.response_json
            """,
            (cache_key, time.time() + CACHE_SECONDS, json.dumps(payload)),
        )
        connection.execute(
            "DELETE FROM public_job_request_claims WHERE cache_key = ?", (cache_key,)
        )


def _release(path: Path, cache_key: str) -> None:
    with _connection(path) as connection:
        connection.execute(
            "DELETE FROM public_job_request_claims WHERE cache_key = ?", (cache_key,)
        )


async def _get_json(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cache_path: Path,
    provider: str,
    url: str,
) -> tuple[Any, bool]:
    cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    deadline = time.monotonic() + 12
    while True:
        state, payload = await asyncio.to_thread(_claim, cache_path, cache_key)
        if state == "cached":
            return payload, True
        if state == "owner":
            break
        if time.monotonic() >= deadline:
            raise ProviderError(504, "A matching provider request is still running; retry shortly.", provider)
        await asyncio.sleep(0.1)

    try:
        async with semaphore:
            response = await client.get(url)
        _development_log(
            "public-job provider=%s response_status=%d",
            provider,
            response.status_code,
        )
        if response.status_code == 429:
            raise ProviderError(429, "The provider rate limit was reached; retry later.", provider)
        if response.status_code in {401, 403}:
            raise ProviderError(502, "The provider declined this public board request.", provider)
        if response.status_code >= 500:
            raise ProviderError(502, "The provider is temporarily unavailable.", provider)
        if response.status_code >= 400:
            raise ProviderError(502, "The public job board could not be retrieved.", provider)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError(502, "The provider returned an oversized response.", provider)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(502, "The provider returned invalid JSON.", provider) from exc
        if not isinstance(payload, (dict, list)):
            raise ProviderError(502, "The provider returned an unexpected response.", provider)
        await asyncio.to_thread(_store, cache_path, cache_key, payload)
        return payload, False
    except ProviderError:
        raise
    except httpx.TimeoutException as exc:
        raise ProviderError(504, "The provider request timed out.", provider) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(502, "Could not reach the public job provider.", provider) from exc
    finally:
        await asyncio.to_thread(_release, cache_path, cache_key)


def _text(value: object) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "").strip()
    if isinstance(value, list):
        return ", ".join(filter(None, (_text(item) for item in value)))
    return str(value or "").strip()


def _utc_date(value: object, *, milliseconds: bool = False) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
        except ValueError:
            return ""
    if milliseconds:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def _salary_text(minimum: object, maximum: object, currency: object, period: object) -> str:
    low, high = _number(minimum), _number(maximum)
    currency_label = str(currency or "").strip().upper()
    period_label = str(period or "").strip().lower()
    if low is None and high is None:
        return ""
    marker = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£", "CAD": "CA$", "AUD": "A$"}.get(
        currency_label, currency_label or "currency unspecified"
    )
    amounts = [
        f"{marker}{int(value):,}" if value.is_integer() else f"{marker}{value:,.2f}"
        for value in (low, high) if value is not None
    ]
    amount = " – ".join(amounts)
    return f"{amount} / {period_label}" if period_label else amount


def _number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _inr_annual_range(
    minimum: object,
    maximum: object,
    currency: object,
    period: object,
) -> tuple[float | None, float | None]:
    low, high = _number(minimum), _number(maximum)
    if str(currency or "").upper() != "INR":
        return None, None
    period_key = str(period or "").casefold()
    multiplier = 12 if any(term in period_key for term in ("month", "monthly")) else (
        1 if any(term in period_key for term in ("year", "annual", "annum")) else None
    )
    if multiplier is None:
        return None, None
    return (
        low * multiplier if low is not None else None,
        high * multiplier if high is not None else None,
    )


def _employment(value: object) -> tuple[str, str]:
    label = _text(value)
    normalized = label.casefold().replace("-", " ")
    if any(term in normalized for term in ("intern", "co op")):
        return "internship", label
    if any(term in normalized for term in ("part time", "parttime")):
        return "", label
    if any(term in normalized for term in ("full time", "fulltime", "regular")):
        return "permanent", label
    if "contract" in normalized or "temporary" in normalized:
        return "contract", label
    return "", label


def _contract_time(value: object) -> str:
    normalized = _text(value).casefold().replace("-", " ")
    if "full time" in normalized or "fulltime" in normalized:
        return "full_time"
    if "part time" in normalized or "parttime" in normalized:
        return "part_time"
    return ""


def _india_eligible(text: str) -> bool:
    normalized = text.casefold()
    return any(re.search(rf"(?<![a-z]){re.escape(term)}(?![a-z])", normalized) for term in _INDIA_LOCATIONS)


def _work_mode(title: str, location: str, description: str = "") -> str:
    listing_details = f"{title} {location}".casefold()
    description = description.casefold()
    if re.search(r"\bhybrid\b", listing_details) or re.search(
        r"\bhybrid (?:role|position|schedule|work model|working model)\b", description
    ):
        return "hybrid"
    if re.search(r"\b(remote|work from home|wfh)\b", listing_details) or re.search(
        r"\b(fully remote|remote[- ]first|remote (?:role|position|job)|"
        r"work from anywhere|100% remote)\b",
        description,
    ):
        return "remote"
    if re.search(
        r"\b(on[- ]site|onsite|in[- ]office|office[- ]based|work from office)\b",
        listing_details,
    ) or re.search(r"\b(on[- ]site role|on[- ]site position|office[- ]based role)\b", description):
        return "on_site"
    return ""


def _india_timezone(value: object) -> bool:
    return (
        _number(value) == 5.5
        or bool(re.search(r"\b(?:utc|gmt)?\s*\+?0?5:30\b", str(value), re.I))
        or bool(re.search(r"\b(?:asia/kolkata|asia/calcutta|india standard time)\b", str(value), re.I))
    )


def _himalayas_job(item: dict[str, Any]) -> dict[str, Any] | None:
    title = _text(item.get("title"))
    application_url = _text(item.get("applicationLink"))
    identifier = _text(item.get("guid")) or application_url
    if not title or not application_url or not identifier:
        return None
    restrictions = item.get("locationRestrictions")
    restrictions = restrictions if isinstance(restrictions, list) else []
    zones = item.get("timezoneRestrictions")
    zones = zones if isinstance(zones, list) else []
    india_eligible: bool | None
    if restrictions:
        india_eligible = any(
            _text(value).casefold() in {"in", "india", "worldwide", "global", "anywhere"}
            or "india" in _text(value).casefold()
            for value in restrictions
        )
    elif zones:
        india_eligible = any(_india_timezone(value) for value in zones)
    else:
        india_eligible = True  # No location or time-zone restrictions in this feed.
    if india_eligible is True:
        location = "Remote · India eligible"
    elif restrictions:
        location = "Remote · " + ", ".join(map(str, restrictions))
    elif zones:
        location = "Remote · " + ", ".join(map(str, zones))
    else:
        location = "Remote · eligibility not specified"

    currency = _text(item.get("currency")).upper()
    period = _text(item.get("salaryPeriod"))
    minimum, maximum = _inr_annual_range(
        item.get("minSalary"), item.get("maxSalary"), currency, period
    )
    seniority = item.get("seniority")
    seniority = [str(value) for value in seniority] if isinstance(seniority, list) else []
    employment, employment_label = _employment(item.get("employmentType"))
    return {
        "id": f"himalayas:{identifier}",
        "title": title,
        "company": _text(item.get("companyName")),
        "location": location,
        "description": _text(item.get("description") or item.get("excerpt")),
        "salary_text": _salary_text(item.get("minSalary"), item.get("maxSalary"), currency, period),
        "salary_min": minimum,
        "salary_max": maximum,
        "salary_currency": currency,
        "salary_period": period,
        "salary_is_predicted": False,
        "created": _utc_date(item.get("pubDate")),
        "employment_type": employment,
        "employment_type_label": employment_label,
        "contract_time": _contract_time(employment_label),
        "category": _text(item.get("parentCategories") or item.get("categories")),
        "work_mode": "remote",
        "application_url": application_url,
        "source": "Himalayas",
        "source_url": "https://himalayas.app/jobs",
        "experience_levels": seniority,
        "location_eligible": india_eligible,
    }


def _greenhouse_job(item: dict[str, Any], board: str) -> dict[str, Any] | None:
    title = _text(item.get("title"))
    application_url = _text(item.get("absolute_url"))
    identifier = _text(item.get("id"))
    if not title or not application_url or not identifier:
        return None
    location = _text(item.get("location"))
    content = _text(item.get("content"))
    department = _text(item.get("departments"))
    offices = _text(item.get("offices"))
    location_text = " · ".join(filter(None, (location, offices)))
    work_mode = _work_mode(title, location_text, content)
    remote = work_mode == "remote"
    india_eligible = _india_eligible(" ".join((title, location_text)))
    if not india_eligible and remote:
        india_eligible = bool(re.search(
            r"(?:remote|work from anywhere|worldwide).{0,80}\bindia\b|\bindia\b.{0,80}(?:remote|eligible|based|open)",
            content,
            re.I | re.S,
        ))
    employment, employment_label = _employment(f"{title} {content}")
    return {
        "id": f"greenhouse:{board}:{identifier}",
        "title": title,
        "company": _text(item.get("company_name")) or board.replace("-", " ").title(),
        "location": location_text or ("Remote" if remote else ""),
        "description": content,
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_is_predicted": False,
        "created": _text(item.get("first_published") or item.get("updated_at")),
        "employment_type": employment,
        "employment_type_label": employment_label,
        "contract_time": _contract_time(employment_label),
        "category": department,
        "work_mode": work_mode,
        "application_url": application_url,
        "source": "Greenhouse",
        "source_url": f"https://boards.greenhouse.io/{board}",
        "experience_levels": [],
        "location_eligible": india_eligible,
    }


def _lever_job(item: dict[str, Any], site: str) -> dict[str, Any] | None:
    title = _text(item.get("text"))
    application_url = _text(item.get("applyUrl") or item.get("hostedUrl"))
    identifier = _text(item.get("id"))
    if not title or not application_url or not identifier:
        return None
    categories = item.get("categories")
    categories = categories if isinstance(categories, dict) else {}
    location = _text(categories.get("allLocations") or categories.get("location"))
    workplace = _text(item.get("workplaceType")).casefold()
    work_mode = {"remote": "remote", "hybrid": "hybrid", "on-site": "on_site", "onsite": "on_site"}.get(
        workplace, ""
    )
    description = _text(item.get("descriptionPlain")) or _text(item.get("description"))
    if not work_mode:
        work_mode = _work_mode(title, location, description)
    india_eligible = _india_eligible(" ".join((title, location)))
    if not india_eligible and work_mode == "remote":
        india_eligible = bool(re.search(
            r"(?:remote|work from anywhere|worldwide).{0,80}\bindia\b|\bindia\b.{0,80}(?:remote|eligible|based|open)",
            description,
            re.I | re.S,
        ))
    commitment = _text(categories.get("commitment"))
    employment, employment_label = _employment(commitment)
    salary = item.get("salaryRange")
    salary = salary if isinstance(salary, dict) else {}
    currency = _text(salary.get("currency")).upper()
    period = _text(salary.get("interval"))
    low, high = _inr_annual_range(salary.get("min"), salary.get("max"), currency, period)
    return {
        "id": f"lever:{site}:{identifier}",
        "title": title,
        "company": {"cred": "CRED"}.get(site.casefold(), site.replace("-", " ").title()),
        "location": location,
        "description": description,
        "salary_text": _salary_text(salary.get("min"), salary.get("max"), currency, period)
        or _text(item.get("salaryDescriptionPlain")),
        "salary_min": low,
        "salary_max": high,
        "salary_currency": currency,
        "salary_period": period,
        "salary_is_predicted": False,
        "created": _utc_date(item.get("createdAt"), milliseconds=True),
        "employment_type": employment,
        "employment_type_label": commitment,
        "contract_time": _contract_time(commitment),
        "category": _text(categories.get("department") or categories.get("team")),
        "work_mode": work_mode,
        "application_url": application_url,
        "source": "Lever",
        "source_url": f"https://jobs.lever.co/{site}",
        "experience_levels": [],
        "location_eligible": india_eligible,
    }


def _warnings(error: ProviderError, provider: str) -> dict[str, str]:
    return {"provider": provider, "error": str(error)}


async def _fetch_himalayas(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cache_path: Path,
    queries: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], bool, int, int]:
    requests = [
        (
            query,
            page,
            f"{HIMALAYAS_SEARCH_URL}?{urlencode({'q': query, 'page': page})}",
        )
        for query in queries
        for page in range(1, HIMALAYAS_PAGES_PER_QUERY + 1)
    ]
    responses = await asyncio.gather(
        *(
            _get_json(client, semaphore, cache_path, "Himalayas", url)
            for _, _, url in requests
        ),
        return_exceptions=True,
    )
    jobs: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    succeeded = 0
    cache_hits = 0
    for (query, _, _), result in zip(requests, responses):
        if isinstance(result, ProviderError):
            warnings.append(_warnings(result, "Himalayas"))
            continue
        if isinstance(result, BaseException):
            raise result
        payload, cached = result
        cache_hits += int(cached)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            warnings.append({"provider": "Himalayas", "error": "The provider returned an invalid job list."})
            continue
        succeeded += 1
        jobs.extend(
            normalized
            for item in payload["jobs"]
            if isinstance(item, dict)
            for normalized in [_himalayas_job(item)]
            if normalized is not None
        )
        if payload.get("totalCount") == 0:
            _LOGGER.debug("Himalayas query returned no matches: %s", query)
    _development_log("public-job provider=Himalayas normalized_count=%d", len(jobs))
    return jobs, warnings, succeeded > 0, cache_hits, len(requests)


async def _fetch_greenhouse(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cache_path: Path,
    boards: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], bool, int, int]:
    if not boards:
        return [], [], False, 0, 0
    requests = [
        (board, f"{GREENHOUSE_BOARD_URL}/{board}/jobs?content=true")
        for board in boards
    ]
    responses = await asyncio.gather(
        *(
            _get_json(client, semaphore, cache_path, "Greenhouse", url)
            for _, url in requests
        ),
        return_exceptions=True,
    )
    jobs: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    succeeded = 0
    cache_hits = 0
    for (board, _), result in zip(requests, responses):
        if isinstance(result, ProviderError):
            warnings.append(_warnings(result, "Greenhouse"))
            continue
        if isinstance(result, BaseException):
            raise result
        payload, cached = result
        cache_hits += int(cached)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            warnings.append({"provider": "Greenhouse", "error": f"Board {board} returned an invalid job list."})
            continue
        succeeded += 1
        jobs.extend(
            normalized
            for item in payload["jobs"]
            if isinstance(item, dict)
            for normalized in [_greenhouse_job(item, board)]
            if normalized is not None
        )
    _development_log("public-job provider=Greenhouse normalized_count=%d", len(jobs))
    return jobs, warnings, succeeded > 0, cache_hits, len(requests)


async def _fetch_lever(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    cache_path: Path,
    sites: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], bool, int, int]:
    if not sites:
        return [], [], False, 0, 0

    async def fetch_site(site: str):
        jobs: list[dict[str, Any]] = []
        warnings: list[dict[str, str]] = []
        cache_hits = 0
        requests = 0
        succeeded = False
        for page in range(LEVER_MAX_PAGES):
            skip = page * LEVER_PAGE_SIZE
            url = (
                f"{LEVER_POSTINGS_URL}/{site}?"
                f"{urlencode({'mode': 'json', 'skip': skip, 'limit': LEVER_PAGE_SIZE})}"
            )
            requests += 1
            try:
                payload, cached = await _get_json(client, semaphore, cache_path, "Lever", url)
            except ProviderError as error:
                warnings.append(_warnings(error, "Lever"))
                break
            cache_hits += int(cached)
            if not isinstance(payload, list):
                warnings.append({
                    "provider": "Lever",
                    "error": f"Board {site} returned an invalid job list.",
                })
                break
            succeeded = True
            jobs.extend(
                normalized
                for item in payload
                if isinstance(item, dict)
                for normalized in [_lever_job(item, site)]
                if normalized is not None
            )
            if len(payload) < LEVER_PAGE_SIZE:
                break
        return jobs, warnings, succeeded, cache_hits, requests

    responses = await asyncio.gather(*(fetch_site(site) for site in sites))
    jobs: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    succeeded = 0
    cache_hits = 0
    request_count = 0
    for site_result in responses:
        site_jobs, site_warnings, site_succeeded, site_cache_hits, site_requests = site_result
        jobs.extend(site_jobs)
        warnings.extend(site_warnings)
        succeeded += int(site_succeeded)
        cache_hits += site_cache_hits
        request_count += site_requests
    _development_log("public-job provider=Lever normalized_count=%d", len(jobs))
    return jobs, warnings, succeeded > 0, cache_hits, request_count


async def fetch_public_listings(
    search_queries: list[str],
    cache_path: Path,
) -> dict[str, Any]:
    """Fetch current public postings concurrently; personal ranking happens locally."""
    boards = greenhouse_boards()
    sites = lever_sites()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(8, connect=4),
        follow_redirects=True,
        headers={
            "Accept": "application/json",
            "User-Agent": "SkillBridge public job search/1.0",
        },
    ) as client:
        results = await asyncio.gather(
            _fetch_himalayas(client, semaphore, cache_path, search_queries),
            _fetch_greenhouse(client, semaphore, cache_path, boards),
            _fetch_lever(client, semaphore, cache_path, sites),
            return_exceptions=True,
        )
    jobs: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    successful_providers = []
    attempted_providers = ["Himalayas"]
    if boards:
        attempted_providers.append("Greenhouse")
    if sites:
        attempted_providers.append("Lever")
    cache_hits = 0
    cache_total = 0
    for provider, result in zip(("Himalayas", "Greenhouse", "Lever"), results):
        if isinstance(result, ProviderError):
            warnings.append(_warnings(result, provider))
            continue
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            _LOGGER.warning("Public job provider %s failed: %s", provider, type(result).__name__)
            warnings.append({"provider": provider, "error": "The provider could not be processed."})
            continue
        if isinstance(result, BaseException):
            raise result
        provider_jobs, provider_warnings, succeeded, hits, requests = result
        jobs.extend(provider_jobs)
        warnings.extend(provider_warnings)
        cache_hits += hits
        cache_total += requests
        if succeeded:
            successful_providers.append(provider)

    for warning in warnings:
        _LOGGER.warning(
            "public-job provider failure provider=%s reason=%s",
            warning.get("provider", "unknown"),
            warning.get("error", "unknown"),
        )

    if not successful_providers:
        message = "All configured public job providers are unavailable; retry shortly."
        if warnings:
            message = " ".join(
                f"{item['provider']}: {item['error']}" for item in warnings[:3]
            )
        raise ProviderError(502, message)

    if os.environ.get("APP_ENV", "").casefold() == "development" or os.environ.get("DEBUG", "").casefold() == "true":
        _LOGGER.info(
            "public-job providers attempted=%s successful=%s raw_count=%d cache_hits=%d warnings=%d",
            ",".join(attempted_providers),
            ",".join(successful_providers),
            len(jobs),
            cache_hits,
            len(warnings),
        )
    return {
        "jobs": jobs,
        "provider_warnings": warnings,
        "providers": successful_providers,
        "cache_hits": cache_hits,
        "cache_total": cache_total,
    }
