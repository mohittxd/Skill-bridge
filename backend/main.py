"""Local FastAPI application for the SkillBridge mini project."""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from functools import lru_cache
from threading import Lock
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Literal, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator
from dotenv import load_dotenv

import job_search
import career_recommendations
from onet_matching import skill_key
from resume_parser import MAX_FILE_SIZE, parse_resume


BACKEND_DIR = Path(__file__).resolve().parent
DATA_DIR = BACKEND_DIR / "data"
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR.parent / ".env")
DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BACKEND_DIR / "skill_gap.db"))
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")
COOKIE_NAME = "skillbridge_session"
SESSION_TTL_HOURS = int(os.environ.get("SESSION_TTL_HOURS", "8"))
SESSION_TTL_SECONDS = SESSION_TTL_HOURS * 60 * 60
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").casefold() == "true"
MAX_FAILED_LOGINS = int(os.environ.get("MAX_FAILED_LOGINS", "5"))
LOGIN_WINDOW_SECONDS = int(os.environ.get("LOGIN_WINDOW_SECONDS", "900"))
PASSWORD_HASHER = PasswordHasher()
_failed_logins: dict[str, list[float]] = {}
_LOGGER = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,50}$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@contextmanager
def database_connection() -> Iterator[sqlite3.Connection]:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def utc_timestamp() -> int:
    return int(time.time())


def _create_sessions_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE sessions (
            token_hash TEXT PRIMARY KEY,
            admin_id INTEGER,
            employee_id INTEGER,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            CHECK ((admin_id IS NOT NULL) != (employee_id IS NOT NULL)),
            FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE,
            FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
        )
        """
    )


def _initialize_onet_catalog_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS onet_occupations (
            code TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category_code TEXT NOT NULL,
            category_name TEXT NOT NULL,
            skill_count INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS onet_occupation_skills (
            occupation_code TEXT NOT NULL,
            skill_key TEXT NOT NULL,
            skill_name TEXT NOT NULL,
            domains TEXT NOT NULL,
            importance REAL,
            required_level REAL,
            hot_technology INTEGER NOT NULL DEFAULT 0,
            in_demand INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (occupation_code, skill_key),
            FOREIGN KEY (occupation_code) REFERENCES onet_occupations(code)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS onet_catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_onet_occupation_title "
        "ON onet_occupations(title COLLATE NOCASE)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_onet_occupation_category "
        "ON onet_occupations(category_code)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_onet_skill_key ON onet_occupation_skills(skill_key)"
    )


def _initialize_esco_catalog_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS esco_occupations (
            uri TEXT PRIMARY KEY,
            preferred_label TEXT NOT NULL,
            alt_labels TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',
            isco_group TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS esco_skills (
            uri TEXT PRIMARY KEY,
            preferred_label TEXT NOT NULL,
            alt_labels TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',
            skill_type TEXT NOT NULL DEFAULT ''
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS esco_occupation_skill_relations (
            occupation_uri TEXT NOT NULL,
            skill_uri TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            skill_type TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (occupation_uri, skill_uri, relation_type),
            FOREIGN KEY (occupation_uri) REFERENCES esco_occupations(uri)
                ON DELETE CASCADE,
            FOREIGN KEY (skill_uri) REFERENCES esco_skills(uri)
                ON DELETE CASCADE
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS esco_concept_embeddings (
            concept_type TEXT NOT NULL CHECK (concept_type IN ('occupation', 'skill')),
            concept_uri TEXT NOT NULL,
            model_name TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL,
            PRIMARY KEY (concept_type, concept_uri, model_name)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS esco_catalog_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_esco_occupation_title "
        "ON esco_occupations(preferred_label COLLATE NOCASE)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_esco_skill_label "
        "ON esco_skills(preferred_label COLLATE NOCASE)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_esco_relation_skill "
        "ON esco_occupation_skill_relations(skill_uri)"
    )


def initialize_database() -> None:
    with database_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                full_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )

        session_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
        ).fetchone()
        if not session_table:
            _create_sessions_table(connection)
        else:
            columns = {
                row["name"]: row
                for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
            }
            legacy_admin_id = columns.get("admin_id")
            needs_migration = (
                "employee_id" not in columns
                or legacy_admin_id is None
                or legacy_admin_id["notnull"]
            )
            if needs_migration:
                old_columns = set(columns)
                connection.execute("ALTER TABLE sessions RENAME TO sessions_legacy")
                _create_sessions_table(connection)
                if {"token_hash", "admin_id", "created_at", "expires_at"}.issubset(old_columns):
                    connection.execute(
                        """
                        INSERT INTO sessions(token_hash, admin_id, created_at, expires_at)
                        SELECT token_hash, admin_id, created_at, expires_at
                        FROM sessions_legacy
                        """
                    )
                connection.execute("DROP TABLE sessions_legacy")

        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions(expires_at)"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_profiles (
                employee_id INTEGER PRIMARY KEY,
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                education TEXT NOT NULL DEFAULT '',
                work_experience TEXT NOT NULL DEFAULT '',
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_skills (
                employee_id INTEGER NOT NULL,
                skill TEXT NOT NULL COLLATE NOCASE,
                current_level INTEGER,
                source TEXT NOT NULL CHECK (source IN ('resume', 'manual')),
                PRIMARY KEY (employee_id, skill),
                CHECK (current_level IS NULL OR current_level BETWEEN 0 AND 5),
                FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS employee_job_preferences (
                employee_id INTEGER PRIMARY KEY,
                preferred_roles TEXT NOT NULL DEFAULT '[]',
                location TEXT NOT NULL DEFAULT 'India',
                experience_level TEXT NOT NULL DEFAULT 'any',
                employment_type TEXT NOT NULL DEFAULT 'any',
                minimum_salary INTEGER,
                career_category TEXT NOT NULL DEFAULT '',
                work_mode TEXT NOT NULL DEFAULT 'any',
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (employee_id) REFERENCES employees(id) ON DELETE CASCADE
            )
            """
        )
        preference_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(employee_job_preferences)"
            ).fetchall()
        }
        if "career_category" not in preference_columns:
            connection.execute(
                "ALTER TABLE employee_job_preferences "
                "ADD COLUMN career_category TEXT NOT NULL DEFAULT ''"
            )
        if "work_mode" not in preference_columns:
            connection.execute(
                "ALTER TABLE employee_job_preferences "
                "ADD COLUMN work_mode TEXT NOT NULL DEFAULT 'any'"
            )
        _initialize_onet_catalog_schema(connection)
        _initialize_esco_catalog_schema(connection)


app = FastAPI(title="Employee Skill Gap Analysis & Training Recommendation System")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def validate_mutation_origin(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and origin != FRONTEND_ORIGIN:
            return Response(
                content='{"detail":"Request origin is not allowed."}',
                status_code=403,
                media_type="application/json",
            )
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    initialize_database()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegistrationInput(StrictModel):
    full_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("full_name", "username")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("This field cannot be blank.")
        return value

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if not USERNAME_PATTERN.fullmatch(value):
            raise ValueError("Use 3-50 letters, numbers, periods, underscores, or hyphens.")
        return value


class LoginInput(StrictModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class SkillInput(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    level: Optional[StrictInt] = Field(default=None, ge=0, le=5)
    source: Literal["resume", "manual"] = "manual"

    @field_validator("name")
    @classmethod
    def trim_skill(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Skill names cannot be blank.")
        return value


class ProfileInput(StrictModel):
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(default="", max_length=254)
    phone: str = Field(default="", max_length=80)
    education: str = Field(default="", max_length=10_000)
    work_experience: str = Field(default="", max_length=20_000)
    skills: list[SkillInput] = Field(default_factory=list, max_length=100)

    @field_validator("full_name", "email", "phone", "education", "work_experience")
    @classmethod
    def trim_profile_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("full_name")
    @classmethod
    def require_profile_name(cls, value: str) -> str:
        if not value:
            raise ValueError("Full name cannot be blank.")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if value and not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value


class JobPreferencesInput(StrictModel):
    preferred_roles: list[str] = Field(default_factory=list, max_length=6)
    location: str = Field(default="India", max_length=120)
    experience_level: Literal["any", "internship", "entry", "mid", "senior"] = "any"
    employment_type: Literal[
        "any", "full_time", "part_time", "internship", "contract", "permanent"
    ] = "any"
    career_category: Literal[
        "",
        "it-jobs",
        "accounting-finance-jobs",
        "engineering-jobs",
        "sales-jobs",
        "customer-services-jobs",
        "hr-jobs",
        "healthcare-nursing-jobs",
        "teaching-jobs",
        "creative-design-jobs",
        "admin-jobs",
        "other-general-jobs",
    ] = ""
    work_mode: Literal["any", "remote", "hybrid", "on_site"] = "any"
    minimum_salary: Optional[StrictInt] = Field(default=None, ge=0, le=1_000_000_000)

    @field_validator("preferred_roles")
    @classmethod
    def clean_preferred_roles(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for value in values:
            title = value.strip()
            if not title:
                continue
            if len(title) > 100:
                raise ValueError("Each preferred role must be 100 characters or fewer.")
            key = title.casefold()
            if key not in seen:
                cleaned.append(title)
                seen.add(key)
        return cleaned

    @field_validator("location")
    @classmethod
    def clean_location(cls, value: str) -> str:
        return value.strip()


def _public_user(row: sqlite3.Row, role: str) -> dict[str, object]:
    user: dict[str, object] = {
        "id": row["id"],
        "username": row["username"],
        "role": role,
    }
    if role == "employee":
        user["full_name"] = row["full_name"]
    return user


def _session_user(token: Optional[str]) -> Optional[dict[str, object]]:
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = utc_timestamp()
    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT s.admin_id, s.employee_id, s.expires_at,
                   a.id AS a_id, a.username AS a_username,
                   e.id AS e_id, e.username AS e_username, e.full_name AS e_full_name
            FROM sessions s
            LEFT JOIN admins a ON a.id = s.admin_id
            LEFT JOIN employees e ON e.id = s.employee_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= now:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        if row["admin_id"] is not None and row["a_id"] is not None:
            return {"id": row["a_id"], "username": row["a_username"], "role": "admin"}
        if row["employee_id"] is not None and row["e_id"] is not None:
            return {
                "id": row["e_id"],
                "username": row["e_username"],
                "full_name": row["e_full_name"],
                "role": "employee",
            }
    return None


def current_user(request: Request) -> dict[str, object]:
    user = _session_user(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="A valid login session is required.")
    return user


def require_admin(user: dict[str, object] = Depends(current_user)) -> dict[str, object]:
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="This endpoint is for HR administrators.")
    return user


def require_employee(user: dict[str, object] = Depends(current_user)) -> dict[str, object]:
    if user["role"] != "employee":
        raise HTTPException(status_code=403, detail="This endpoint is for employees.")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=SESSION_COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _create_session(
    response: Response, connection: sqlite3.Connection, *, admin_id: Optional[int] = None,
    employee_id: Optional[int] = None
) -> None:
    token = secrets.token_urlsafe(32)
    now = utc_timestamp()
    connection.execute(
        """
        INSERT INTO sessions(token_hash, admin_id, employee_id, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
            admin_id,
            employee_id,
            now,
            now + SESSION_TTL_SECONDS,
        ),
    )
    _set_session_cookie(response, token)


def _load_csv(filename: str) -> list[dict[str, str]]:
    try:
        with (DATA_DIR / filename).open("r", encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))
    except OSError as exc:
        raise RuntimeError(f"Required project data file is unavailable: {filename}") from exc


def _role_catalog() -> list[dict[str, object]]:
    roles: dict[str, dict[str, object]] = {}
    for row in _load_csv("role_requirements.csv"):
        role = roles.setdefault(
            row["role_id"],
            {"id": row["role_id"], "name": row["role_name"], "required_skills": 0},
        )
        role["required_skills"] = int(role["required_skills"]) + 1
    return list(roles.values())


def _resume_skill_catalog() -> list[str]:
    skills: dict[str, str] = {}
    for row in _load_csv("role_requirements.csv"):
        skills.setdefault(row["skill"].casefold(), row["skill"])
    for row in _load_csv("courses.csv"):
        skills.setdefault(row["skill"].casefold(), row["skill"])
    return list(skills.values())


def _skill_catalog() -> list[str]:
    skills = {skill.casefold(): skill for skill in _resume_skill_catalog()}
    with database_connection() as connection:
        catalog_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'onet_occupation_skills'"
        ).fetchone()
        if catalog_exists:
            for row in connection.execute(
                """
                SELECT DISTINCT skill_name FROM onet_occupation_skills
                WHERE domains LIKE '%essential%' OR domains LIKE '%transferable%'
                ORDER BY skill_name COLLATE NOCASE
                """
            ):
                skills.setdefault(row["skill_name"].casefold(), row["skill_name"])
    return list(skills.values())


def _profile_for_employee(employee_id: int) -> tuple[dict[str, object], bool]:
    with database_connection() as connection:
        employee = connection.execute(
            "SELECT full_name FROM employees WHERE id = ?", (employee_id,)
        ).fetchone()
        if employee is None:
            raise HTTPException(status_code=404, detail="Employee profile was not found.")
        profile_row = connection.execute(
            "SELECT email, phone, education, work_experience, updated_at "
            "FROM employee_profiles WHERE employee_id = ?",
            (employee_id,),
        ).fetchone()
        skill_rows = connection.execute(
            """
            SELECT skill, current_level, source
            FROM employee_skills WHERE employee_id = ? ORDER BY rowid
            """,
            (employee_id,),
        ).fetchall()

    saved = profile_row is not None
    profile: dict[str, object] = {
        "full_name": employee["full_name"],
        "email": profile_row["email"] if saved else "",
        "phone": profile_row["phone"] if saved else "",
        "education": profile_row["education"] if saved else "",
        "work_experience": profile_row["work_experience"] if saved else "",
        "skills": [
            {"name": row["skill"], "level": row["current_level"], "source": row["source"]}
            for row in skill_rows
        ],
        "updated_at": profile_row["updated_at"] if saved else None,
    }
    return profile, saved


def _job_preferences_for_employee(employee_id: int) -> tuple[dict[str, object], bool]:
    with database_connection() as connection:
        row = connection.execute(
            """
            SELECT preferred_roles, location, experience_level, employment_type,
                   minimum_salary, career_category, work_mode, updated_at
            FROM employee_job_preferences WHERE employee_id = ?
            """,
            (employee_id,),
        ).fetchone()
    if row is None:
        return {
            "preferred_roles": [],
            "location": "India",
            "experience_level": "any",
            "employment_type": "any",
            "minimum_salary": None,
            "career_category": "",
            "work_mode": "any",
            "updated_at": None,
        }, False
    try:
        preferred_roles = json.loads(row["preferred_roles"])
    except json.JSONDecodeError as exc:
        raise RuntimeError("Stored job preferences are invalid.") from exc
    if not isinstance(preferred_roles, list):
        raise RuntimeError("Stored job preferences are invalid.")
    return {
        "preferred_roles": preferred_roles,
        "location": row["location"],
        "experience_level": row["experience_level"],
        "employment_type": row["employment_type"],
        "minimum_salary": row["minimum_salary"],
        "career_category": row["career_category"],
        "work_mode": row["work_mode"],
        "updated_at": row["updated_at"],
    }, True


def _analysis(
    employee: dict[str, object],
    role_id: str,
    ratings: dict[str, tuple[Optional[int], str]],
) -> dict[str, object]:
    requirements = [
        row for row in _load_csv("role_requirements.csv") if row["role_id"] == role_id
    ]
    if not requirements:
        raise HTTPException(status_code=404, detail="Target role was not found.")

    courses_by_skill: dict[str, list[dict[str, object]]] = {}
    for row in _load_csv("courses.csv"):
        courses_by_skill.setdefault(row["skill"].casefold(), []).append(
            {
                "id": row["course_id"],
                "skill": row["skill"],
                "title": row["title"],
                "difficulty": row["difficulty"],
                "estimated_duration": row["estimated_duration"],
            }
        )

    skills: list[dict[str, object]] = []
    recommendations: list[dict[str, object]] = []
    for requirement in requirements:
        skill_name = requirement["skill"]
        required_level = int(requirement["required_level"])
        rating = ratings.get(skill_name.casefold())
        current_level, source = rating if rating else (None, "")
        assessed = current_level is not None
        calculated_level = current_level if assessed else 0
        gap = max(0, required_level - calculated_level)
        mentioned = source == "resume"
        skill = {
            "skill": skill_name,
            "current_level": calculated_level,
            "current_label": calculated_level if assessed else "Unassessed",
            "required_level": required_level,
            "gap": gap,
            "assessed": assessed,
            "rating_source": "Self-reported" if assessed and source in {"resume", "manual"} else None,
            "mentioned": mentioned,
            "evidence_source": "resume" if mentioned else None,
        }
        skills.append(skill)
        if gap:
            preferred_difficulties = (
                {"Beginner": 0, "Intermediate": 1, "Advanced": 2}
                if calculated_level <= 2
                else {"Intermediate": 0, "Advanced": 1, "Beginner": 2}
            )
            courses = sorted(
                courses_by_skill.get(skill_name.casefold(), []),
                key=lambda course: (
                    preferred_difficulties.get(str(course["difficulty"]), 3),
                    str(course["title"]).casefold(),
                ),
            )
            recommendations.append(
                {
                    "skill": skill_name,
                    "current_level": calculated_level,
                    "current_label": calculated_level if assessed else "Unassessed",
                    "required_level": required_level,
                    "gap": gap,
                    "assessed": assessed,
                    "rating_source": "Self-reported" if assessed and source in {"resume", "manual"} else None,
                    "mentioned": mentioned,
                    "courses": courses,
                }
            )

    skills_met = sum(1 for skill in skills if skill["gap"] == 0)
    total = len(skills)
    return {
        "employee": employee,
        "role": {"id": role_id, "name": requirements[0]["role_name"]},
        "skills": skills,
        "summary": {
            "skills_met": skills_met,
            "total_required_skills": total,
            "skills_met_percentage": round(skills_met / total * 100) if total else 0,
            "skills_with_gaps": sum(1 for skill in skills if skill["gap"] > 0),
        },
        "recommendations": recommendations,
    }


@app.post("/api/auth/register", status_code=201)
def register(payload: RegistrationInput, response: Response) -> dict[str, object]:
    password_hash = PASSWORD_HASHER.hash(payload.password)
    now = utc_timestamp()
    try:
        with database_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            admin = connection.execute(
                "SELECT 1 FROM admins WHERE username = ? COLLATE NOCASE",
                (payload.username,),
            ).fetchone()
            existing_employee = connection.execute(
                "SELECT 1 FROM employees WHERE username = ? COLLATE NOCASE",
                (payload.username,),
            ).fetchone()
            if admin or existing_employee:
                raise HTTPException(status_code=409, detail="That username is already in use.")
            cursor = connection.execute(
                """
                INSERT INTO employees(username, full_name, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (payload.username, payload.full_name, password_hash, now),
            )
            employee_id = cursor.lastrowid
            _create_session(response, connection, employee_id=employee_id)
            user = {
                "id": employee_id,
                "username": payload.username,
                "full_name": payload.full_name,
                "role": "employee",
            }
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="That username is already in use.") from exc
    return {"user": user}


@app.post("/api/auth/login")
def login(payload: LoginInput, response: Response) -> dict[str, object]:
    username_key = payload.username.strip().casefold()
    now = time.monotonic()
    recent_failures = [
        attempt for attempt in _failed_logins.get(username_key, [])
        if now - attempt < LOGIN_WINDOW_SECONDS
    ]
    if len(recent_failures) >= MAX_FAILED_LOGINS:
        _failed_logins[username_key] = recent_failures
        raise HTTPException(status_code=429, detail="Too many failed sign-in attempts. Try again later.")

    with database_connection() as connection:
        admin = connection.execute(
            "SELECT id, username, password_hash FROM admins WHERE username = ? COLLATE NOCASE",
            (payload.username.strip(),),
        ).fetchone()
        employee = connection.execute(
            "SELECT id, username, full_name, password_hash FROM employees "
            "WHERE username = ? COLLATE NOCASE",
            (payload.username.strip(),),
        ).fetchone()
        account = admin or employee
        try:
            verified = bool(account) and PASSWORD_HASHER.verify(
                account["password_hash"], payload.password
            )
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            verified = False
        if not verified:
            _failed_logins[username_key] = recent_failures + [now]
            raise HTTPException(status_code=401, detail="Invalid username or password.")
        _failed_logins.pop(username_key, None)
        if admin:
            user = _public_user(admin, "admin")
            _create_session(response, connection, admin_id=admin["id"])
        else:
            user = _public_user(employee, "employee")
            _create_session(response, connection, employee_id=employee["id"])
    return {"user": user}


@app.get("/api/auth/me")
def auth_me(user: dict[str, object] = Depends(current_user)) -> dict[str, object]:
    return {"user": user}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response) -> dict[str, str]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with database_connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
    response.delete_cookie(
        key=COOKIE_NAME, path="/", httponly=True, secure=SESSION_COOKIE_SECURE, samesite="lax"
    )
    return {"message": "Signed out."}


@app.get("/api/employees")
def list_employees(_: dict[str, object] = Depends(require_admin)) -> dict[str, object]:
    return {"employees": _load_csv("employees.csv")}


@app.get("/api/roles")
def list_roles(_: dict[str, object] = Depends(current_user)) -> dict[str, object]:
    return {"roles": _role_catalog()}


@app.get("/api/skills")
def list_skills(_: dict[str, object] = Depends(require_employee)) -> dict[str, object]:
    return {"skills": _skill_catalog()}


@app.get("/api/analysis")
def admin_analysis(
    employee_id: str,
    role_id: str,
    _: dict[str, object] = Depends(require_admin),
) -> dict[str, object]:
    employee = next(
        (row for row in _load_csv("employees.csv") if row["employee_id"] == employee_id),
        None,
    )
    if employee is None:
        raise HTTPException(status_code=404, detail="Employee was not found.")
    ratings = {
        row["skill"].casefold(): (int(row["current_level"]), "sample")
        for row in _load_csv("skill_ratings.csv")
        if row["employee_id"] == employee_id
    }
    return _analysis(employee, role_id, ratings)


@app.get("/api/profile")
def get_profile(user: dict[str, object] = Depends(require_employee)) -> dict[str, object]:
    profile, saved = _profile_for_employee(int(user["id"]))
    return {"profile": profile, "saved": saved}


@app.get("/api/job-preferences")
def get_job_preferences(
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    preferences, saved = _job_preferences_for_employee(int(user["id"]))
    return {"preferences": preferences, "saved": saved}


@app.put("/api/job-preferences")
def save_job_preferences(
    payload: JobPreferencesInput,
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    employee_id = int(user["id"])
    now = utc_timestamp()
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO employee_job_preferences(
                employee_id, preferred_roles, location, experience_level,
                employment_type, minimum_salary, career_category, work_mode, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                preferred_roles = excluded.preferred_roles,
                location = excluded.location,
                experience_level = excluded.experience_level,
                employment_type = excluded.employment_type,
                minimum_salary = excluded.minimum_salary,
                career_category = excluded.career_category,
                work_mode = excluded.work_mode,
                updated_at = excluded.updated_at
            """,
            (
                employee_id,
                json.dumps(payload.preferred_roles),
                payload.location,
                payload.experience_level,
                payload.employment_type,
                payload.minimum_salary,
                payload.career_category,
                payload.work_mode,
                now,
            ),
        )
    preferences, _ = _job_preferences_for_employee(employee_id)
    return {"preferences": preferences, "saved": True}


@app.put("/api/profile")
def save_profile(
    payload: ProfileInput,
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    employee_id = int(user["id"])
    seen_skills: set[str] = set()
    for skill in payload.skills:
        key = skill.name.casefold()
        if key in seen_skills:
            raise HTTPException(status_code=422, detail=f"Duplicate skill: {skill.name}.")
        seen_skills.add(key)

    now = utc_timestamp()
    with database_connection() as connection:
        connection.execute(
            """
            INSERT INTO employee_profiles(
                employee_id, email, phone, education, work_experience, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(employee_id) DO UPDATE SET
                email = excluded.email,
                phone = excluded.phone,
                education = excluded.education,
                work_experience = excluded.work_experience,
                updated_at = excluded.updated_at
            """,
            (
                employee_id,
                payload.email,
                payload.phone,
                payload.education,
                payload.work_experience,
                now,
            ),
        )
        connection.execute(
            "UPDATE employees SET full_name = ? WHERE id = ?",
            (payload.full_name, employee_id),
        )
        connection.execute("DELETE FROM employee_skills WHERE employee_id = ?", (employee_id,))
        connection.executemany(
            """
            INSERT INTO employee_skills(employee_id, skill, current_level, source)
            VALUES (?, ?, ?, ?)
            """,
            [
                (employee_id, skill.name, skill.level, skill.source)
                for skill in payload.skills
            ],
        )
    profile, _ = _profile_for_employee(employee_id)
    return {"profile": profile, "saved": True}


@app.post("/api/resume/parse")
async def parse_uploaded_resume(
    file: UploadFile = File(...),
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    del user
    try:
        suffix = Path(file.filename or "").suffix.casefold()
        if suffix not in {".pdf", ".docx"}:
            raise HTTPException(status_code=415, detail="Only PDF and DOCX resume files are supported.")
        contents = await file.read(MAX_FILE_SIZE + 1)
        if len(contents) > MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="Resume files must be 5 MiB or smaller.")
        try:
            return parse_resume(contents, file.filename or "", _resume_skill_catalog())
        except ValueError as exc:
            message = str(exc)
            status_code = 413 if "5 MiB" in message else 400
            raise HTTPException(status_code=status_code, detail=message) from exc
    finally:
        await file.close()


@app.get("/api/my-analysis")
def employee_analysis(
    role_id: str,
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    employee_id = int(user["id"])
    profile, saved = _profile_for_employee(employee_id)
    if not saved:
        raise HTTPException(status_code=400, detail="Save your profile before running an analysis.")
    ratings = {
        skill["name"].casefold(): (skill["level"], str(skill["source"]))
        for skill in profile["skills"]
    }
    employee = {
        "id": employee_id,
        "name": profile["full_name"],
        "full_name": profile["full_name"],
        "title": "Employee",
        "department": "Personal workspace",
    }
    return _analysis(employee, role_id, ratings)


def _saved_profile_for_matching(employee_id: int) -> dict[str, object]:
    profile, saved = _profile_for_employee(employee_id)
    if not saved:
        raise HTTPException(
            status_code=400,
            detail="Save your confirmed profile and skills before finding matching roles.",
        )
    return profile


def _onet_skill_rows() -> list[sqlite3.Row]:
    with database_connection() as connection:
        imported = connection.execute(
            "SELECT value FROM onet_catalog_metadata WHERE key = 'occupation_count'"
        ).fetchone()
        if imported is None:
            raise HTTPException(
                status_code=503,
                detail="The O*NET role catalog is not imported. Run python import_onet.py from backend/.",
            )
        return connection.execute(
            """
            SELECT o.code, o.title, o.description, o.category_code, o.category_name,
                   s.skill_key, s.skill_name, s.domains, s.importance, s.required_level,
                   s.hot_technology, s.in_demand
            FROM onet_occupations AS o
            JOIN onet_occupation_skills AS s ON s.occupation_code = o.code
            ORDER BY o.code, s.skill_key
            """
        ).fetchall()


def _matching_profile_skills(
    profile: dict[str, object],
) -> dict[str, dict[str, object]]:
    matched: dict[str, dict[str, object]] = {}
    for skill in profile["skills"]:
        key = skill_key(str(skill["name"]))
        current = matched.get(key)
        if current is None or (
            current.get("level") is None and skill.get("level") is not None
        ):
            matched[key] = skill
    return matched


def _role_skill_weights(
    rows: list[sqlite3.Row], profile_skills: dict[str, dict[str, object]]
) -> dict[str, dict[str, object]]:
    by_occupation: dict[str, dict[str, object]] = {}
    for row in rows:
        role = by_occupation.setdefault(
            row["code"],
            {
                "id": row["code"],
                "title": row["title"],
                "name": row["title"],
                "description": row["description"],
                "category_code": row["category_code"],
                "category": row["category_name"],
                "skills": [],
                "matched_weight": 0.0,
                "total_weight": 0.0,
                "matched_skills": [],
                "unconfirmed_skills": [],
            },
        )
        item = {
            "skill": row["skill_name"],
            "skill_key": row["skill_key"],
            "domains": row["domains"],
            "importance": row["importance"],
            "required_level": row["required_level"],
            "hot_technology": bool(row["hot_technology"]),
            "in_demand": bool(row["in_demand"]),
        }
        role["skills"].append(item)
        weight = float(row["importance"]) if row["importance"] is not None else 1.0
        role["total_weight"] = float(role["total_weight"]) + weight
        if row["skill_key"] in profile_skills:
            role["matched_weight"] = float(role["matched_weight"]) + weight
            role["matched_skills"].append(row["skill_name"])
        else:
            role["unconfirmed_skills"].append(row["skill_name"])

    for role in by_occupation.values():
        total_weight = float(role["total_weight"])
        role["skill_match"] = round(
            float(role["matched_weight"]) / total_weight * 100, 1
        ) if total_weight else 0.0
        role["matched_count"] = len(role["matched_skills"])
        role["unconfirmed_count"] = len(role["unconfirmed_skills"])
    return by_occupation


def _course_lookup() -> dict[str, list[dict[str, object]]]:
    courses: dict[str, list[dict[str, object]]] = {}
    for row in _load_csv("courses.csv"):
        courses.setdefault(skill_key(row["skill"]), []).append(
            {
                "id": row["course_id"],
                "skill": row["skill"],
                "title": row["title"],
                "difficulty": row["difficulty"],
                "estimated_duration": row["estimated_duration"],
            }
        )
    return courses


def _onet_role_analysis(
    role: dict[str, object],
    profile: dict[str, object],
    profile_skills: dict[str, dict[str, object]],
) -> dict[str, object]:
    skill_results: list[dict[str, object]] = []
    recommendations: list[dict[str, object]] = []
    courses = _course_lookup()
    comparable_count = 0
    assessed_comparable_count = 0
    skills_met = 0
    focus_count = 0

    for role_skill in role["skills"]:
        profile_skill = profile_skills.get(str(role_skill["skill_key"]))
        confirmed = profile_skill is not None
        level = profile_skill.get("level") if profile_skill else None
        assessed = level is not None
        original_level = role_skill["required_level"]
        required_level = (
            round(float(original_level) * 5 / 7, 1)
            if original_level is not None
            else None
        )
        comparable = assessed and required_level is not None
        gap = round(max(0.0, required_level - int(level)), 1) if comparable else None
        if required_level is not None:
            comparable_count += 1
            if assessed:
                assessed_comparable_count += 1
                if gap == 0:
                    skills_met += 1

        mentioned = bool(profile_skill and profile_skill.get("source") == "resume")
        if not confirmed:
            needs_attention = True
            reason = "This role skill is not confirmed in your saved profile."
            current_label = "Not confirmed"
        elif not assessed and required_level is not None:
            needs_attention = True
            reason = "Self-rate this skill to compare it with the role level."
            current_label = "Unassessed"
        elif gap is not None and gap > 0:
            needs_attention = True
            reason = "Your self-reported level is below the estimated role level."
            current_label = int(level)
            status_label = "Needs focus"
        else:
            needs_attention = False
            reason = "No numeric proficiency comparison is available for this skill."
            current_label = int(level) if assessed else "Unassessed"
            status_label = (
                "Requirement met"
                if gap == 0
                else "No comparable level" if assessed
                else "Unassessed"
            )
        if not confirmed:
            status_label = "Not confirmed"
        elif not assessed and required_level is not None:
            status_label = "Unassessed"

        result = {
            "skill": role_skill["skill"],
            "current_level": int(level) if assessed else 0,
            "current_label": current_label,
            "required_level": required_level,
            "required_label": (
                f"O*NET {float(original_level):.1f}/7 ≈ {required_level:.1f}/5"
                if original_level is not None
                else "No numeric O*NET level available"
            ),
            "importance": role_skill["importance"],
            "importance_label": (
                f"Importance {float(role_skill['importance']):.1f}/5"
                if role_skill["importance"] is not None
                else "Equal-weight software example"
            ),
            "domains": role_skill["domains"],
            "gap": gap,
            "assessed": assessed,
            "confirmed": confirmed,
            "comparison_available": comparable,
            "needs_attention": needs_attention,
            "status_label": status_label,
            "attention_reason": reason,
            "rating_source": "Self-reported" if assessed else None,
            "mentioned": mentioned,
            "evidence_source": profile_skill.get("source") if profile_skill else None,
        }
        skill_results.append(result)
        if needs_attention:
            focus_count += 1
            recommendations.append(
                {
                    "skill": role_skill["skill"],
                    "current_level": int(level) if assessed else None,
                    "current_label": current_label,
                    "required_level": required_level,
                    "gap": gap,
                    "assessed": assessed,
                    "rating_source": "Self-reported" if assessed else None,
                    "mentioned": mentioned,
                    "reason": reason,
                    "courses": courses.get(str(role_skill["skill_key"]), []),
                }
            )

    analysis_employee = {
        "id": int(profile.get("employee_id", 0)),
        "name": profile["full_name"],
        "full_name": profile["full_name"],
        "title": "Employee",
        "department": "Personal workspace",
    }
    return {
        "employee": analysis_employee,
        "role": {
            "id": role["id"],
            "name": role["title"],
            "title": role["title"],
            "description": role["description"],
            "category": role["category"],
        },
        "skill_match": {
            "score": role["skill_match"],
            "matched_count": role["matched_count"],
            "role_skill_count": len(role["skills"]),
            "unconfirmed_count": role["unconfirmed_count"],
            "matched_skills": role["matched_skills"],
            "unconfirmed_skills": role["unconfirmed_skills"],
            "explanation": (
                "Skill match is the share of this occupation's mapped skill weight "
                "covered by confirmed profile skills. O*NET Importance values weight "
                "rated skills; software examples without an Importance rating count equally."
            ),
        },
        "skills": skill_results,
        "summary": {
            "skills_met": skills_met,
            "total_required_skills": assessed_comparable_count,
            "comparable_skill_count": comparable_count,
            "assessed_comparable_skill_count": assessed_comparable_count,
            "skills_met_percentage": (
                round(skills_met / assessed_comparable_count * 100)
                if assessed_comparable_count
                else None
            ),
            "skills_with_gaps": focus_count,
        },
        "recommendations": recommendations,
    }


@app.get("/api/matching/roles")
def find_matching_roles(
    query: str = "",
    category_code: str = "",
    offset: int = 0,
    limit: int = 10,
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset must be zero or greater.")
    if limit < 1 or limit > 50:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 50.")
    profile = _saved_profile_for_matching(int(user["id"]))
    profile_skills = _matching_profile_skills(profile)
    role_catalog = _role_skill_weights(_onet_skill_rows(), profile_skills)
    normalized_query = query.strip().casefold()
    all_roles = list(role_catalog.values())
    categories = sorted(
        {
            (str(role["category_code"]), str(role["category"]))
            for role in all_roles
        },
        key=lambda item: (item[1].casefold(), item[0]),
    )
    filtered = [
        role for role in all_roles
        if (not normalized_query or normalized_query in str(role["title"]).casefold())
        and (not category_code or role["category_code"] == category_code)
    ]
    filtered.sort(
        key=lambda role: (
            -float(role["skill_match"]),
            str(role["title"]).casefold(),
            str(role["id"]),
        )
    )
    page = filtered[offset:offset + limit]
    return {
        "roles": [
            {
                "id": role["id"],
                "title": role["title"],
                "description": role["description"],
                "category_code": role["category_code"],
                "category": role["category"],
                "skill_match": role["skill_match"],
                "matched_skills": role["matched_skills"],
                "unconfirmed_skills": role["unconfirmed_skills"],
                "matched_count": role["matched_count"],
                "unconfirmed_count": role["unconfirmed_count"],
            }
            for role in page
        ],
        "considered": len(all_roles),
        "total": len(filtered),
        "offset": offset,
        "limit": limit,
        "categories": [
            {"code": code, "name": name} for code, name in categories
        ],
        "skill_match_label": "Skill match",
    }


@app.get("/api/matching/roles/{occupation_code}")
def get_matching_role(
    occupation_code: str,
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    profile = _saved_profile_for_matching(int(user["id"]))
    profile_skills = _matching_profile_skills(profile)
    role_catalog = _role_skill_weights(_onet_skill_rows(), profile_skills)
    role = role_catalog.get(occupation_code)
    if role is None:
        raise HTTPException(status_code=404, detail="No O*NET role with usable skill data was found.")
    analysis = _onet_role_analysis(role, profile, profile_skills)
    analysis["employee"]["id"] = int(user["id"])
    attention_count = sum(1 for skill in analysis["skills"] if skill["needs_attention"])
    analysis["summary"]["skills_with_gaps"] = attention_count
    return analysis


def _job_skill_catalog() -> list[str]:
    skills = {name.casefold(): name for name in _resume_skill_catalog()}
    with database_connection() as connection:
        for row in connection.execute(
            "SELECT DISTINCT skill_name FROM onet_occupation_skills ORDER BY skill_name COLLATE NOCASE"
        ):
            skills.setdefault(row["skill_name"].casefold(), row["skill_name"])
    return list(skills.values())


def _recommended_job_titles(
    profile_skills: dict[str, dict[str, object]],
) -> list[str]:
    try:
        rows = _onet_skill_rows()
    except HTTPException as exc:
        if exc.status_code == 503:
            return []
        raise
    roles = _role_skill_weights(rows, profile_skills)
    ranked = [role for role in roles.values() if int(role["matched_count"]) > 0]
    ranked.sort(
        key=lambda role: (
            -float(role["skill_match"]),
            -int(role["matched_count"]),
            str(role["title"]).casefold(),
            str(role["id"]),
        )
    )
    return [str(role["title"]) for role in ranked[:3]]


_ESCO_LOAD_LOCK = Lock()


def _esco_catalog_for_recommendations():
    with _ESCO_LOAD_LOCK:
        return _cached_esco_catalog(str(DATABASE_PATH))


@lru_cache(maxsize=1)
def _cached_esco_catalog(database_path: str) -> tuple[
    list[sqlite3.Row],
    list[sqlite3.Row],
    list[sqlite3.Row],
    dict[str, dict[str, object]],
]:
    with database_connection() as connection:
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM esco_catalog_metadata")
        }
        occupations = connection.execute(
            """
            SELECT uri, preferred_label, alt_labels, description, isco_group
            FROM esco_occupations ORDER BY uri
            """
        ).fetchall()
        skills = connection.execute(
            """
            SELECT uri, preferred_label, alt_labels, description, skill_type
            FROM esco_skills ORDER BY uri
            """
        ).fetchall()
        relations = connection.execute(
            """
            SELECT occupation_uri, skill_uri, relation_type, skill_type
            FROM esco_occupation_skill_relations
            ORDER BY occupation_uri, skill_uri, relation_type
            """
        ).fetchall()
        if not metadata.get("version") or not occupations or not skills or not relations:
            raise HTTPException(
                status_code=503,
                detail=(
                    "The local ESCO catalog is missing or incomplete. Download the English "
                    "CSV classification and run `python import_esco.py` from backend/."
                ),
            )
        if (
            metadata.get("embeddings_ready") != "true"
            or metadata.get("embedding_model") != career_recommendations.MODEL_NAME
        ):
            raise HTTPException(
                status_code=503,
                detail=(
                    "ESCO occupation embeddings are not ready. Run "
                    "`python prepare_esco_embeddings.py` from backend/."
                ),
            )
        try:
            embeddings = career_recommendations.cached_catalog_embeddings(
                connection,
                {str(row["uri"]) for row in occupations},
                {str(row["uri"]) for row in skills},
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return occupations, skills, relations, embeddings


def _career_recommendations_for_employee(
    employee_id: int,
    query: str,
    limit: Optional[int] = 10,
    category: str = "",
) -> dict[str, object]:
    profile = _saved_profile_for_matching(employee_id)
    profile_skills = _matching_profile_skills(profile)
    preferences, _ = _job_preferences_for_employee(employee_id)
    occupations, skills, relations, embeddings = _esco_catalog_for_recommendations()
    categories = sorted({
        str(row["isco_group"]).strip()
        for row in occupations
        if str(row["isco_group"]).strip()
    })
    try:
        result = career_recommendations.recommend_career_roles(
            occupations,
            skills,
            relations,
            embeddings,
            profile_skills,
            education=str(profile["education"]),
            work_experience=str(profile["work_experience"]),
            interests=[str(role) for role in preferences["preferred_roles"]],
            query=query,
            limit=(
                len(occupations)
                if limit is None or category.strip()
                else limit
            ),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    category_filter = category.strip().casefold()
    if category_filter:
        filtered_roles = [
            role for role in result["roles"]
            if category_filter in str(role["category_code"]).casefold()
            or category_filter in str(role["category"]).casefold()
        ]
        result["roles"] = filtered_roles[:limit] if limit is not None else filtered_roles
        result["result_count"] = len(filtered_roles)
        result["total"] = len(filtered_roles)
        if not filtered_roles:
            result["message"] = "No ESCO occupations matched this category and search."
    with database_connection() as connection:
        release = connection.execute(
            "SELECT value FROM esco_catalog_metadata WHERE key = 'version'"
        ).fetchone()
    return {
        **result,
        "source": "ESCO",
        "release": release["value"] if release else None,
        "categories": [
            {"code": code, "name": f"ISCO {code}"}
            for code in categories
        ],
    }


@app.get("/api/career-recommendations")
def recommend_career_roles(
    query: str = Query(default="", max_length=120),
    limit: int = Query(default=10, ge=1, le=10),
    category: str = Query(default="", max_length=80),
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    return _career_recommendations_for_employee(
        int(user["id"]),
        query,
        limit,
        category,
    )


@app.get("/api/matches")
def find_career_matches_compatibility(
    q: str = Query(default="", max_length=120),
    category: str = Query(default="", max_length=80),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    result = _career_recommendations_for_employee(
        int(user["id"]),
        q,
        limit=None,
        category=category,
    )
    filtered_roles = result["roles"]
    start = (page - 1) * page_size
    page_roles = filtered_roles[start:start + page_size]
    return {
        **result,
        "roles": page_roles,
        "matches": page_roles,
        "total": len(filtered_roles),
        "result_count": len(filtered_roles),
        "page": page,
        "page_size": page_size,
        "pages": (len(filtered_roles) + page_size - 1) // page_size,
    }


@app.get("/api/jobs")
async def search_live_jobs(
    query: str = Query(default="", max_length=120),
    page: int = 1,
    per_page: int = 10,
    sort_by: Literal["relevance", "date", "salary_asc", "salary_desc"] = "relevance",
    user: dict[str, object] = Depends(require_employee),
) -> dict[str, object]:
    if page < 1:
        raise HTTPException(status_code=422, detail="page must be one or greater.")
    if per_page < 1 or per_page > 50:
        raise HTTPException(status_code=422, detail="per_page must be between 1 and 50.")

    employee_id = int(user["id"])
    profile = _saved_profile_for_matching(employee_id)
    preferences, _ = _job_preferences_for_employee(employee_id)
    profile_skills = _matching_profile_skills(profile)
    skill_names = [str(skill["name"]) for skill in profile["skills"]]
    preferred_roles = list(dict.fromkeys(
        str(role).strip() for role in preferences["preferred_roles"] if str(role).strip()
    ))
    manual_query = query.strip()
    target_titles = [manual_query] if manual_query else preferred_roles
    if not target_titles:
        try:
            recommendations = await run_in_threadpool(
                _career_recommendations_for_employee, employee_id, "", 2,
            )
            target_titles = [role["title"] for role in recommendations["roles"]]
        except HTTPException:
            # ESCO setup failures should not disable explicit skill-based vacancy search.
            target_titles = []
        if not target_titles:
            target_titles = job_search.infer_profile_roles(skill_names) or skill_names[:2]
    experience_level = str(preferences["experience_level"])
    if experience_level == "any":
        experience_level = job_search.profile_experience_level(str(profile["work_experience"]))
    query_experience = (
        "internship"
        if preferences["employment_type"] == "internship"
        else experience_level
    )
    search_queries = job_search.generate_search_queries(
        target_titles,
        query_experience,
        limit=3,
    )
    provider_roles = target_titles
    if not search_queries:
        raise HTTPException(
            status_code=400,
            detail="Add a preferred job role or confirmed skills before searching.",
        )

    if (
        os.environ.get("APP_ENV", "").casefold() == "development"
        or os.environ.get("DEBUG", "").casefold() == "true"
    ):
        _LOGGER.info(
            "job-search profile preferences=%s skills=%s experience=%s",
            json.dumps({
                key: preferences[key]
                for key in (
                    "preferred_roles", "location", "experience_level", "employment_type",
                    "minimum_salary", "career_category", "work_mode",
                )
            }, sort_keys=True),
            json.dumps(skill_names),
            experience_level or "unknown",
        )
        _LOGGER.info("job-search generated-queries=%s", json.dumps(search_queries))

    try:
        result, cached = await job_search.search_listings(
            cache_path=DATABASE_PATH.parent / "public_job_cache.sqlite3",
            search_queries=search_queries,
            target_titles=target_titles,
            skill_names=skill_names,
        )
    except job_search.JobSearchProviderError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    requested_senior = (
        experience_level == "senior"
        or any(
            re.search(r"\b(senior|lead|principal|staff)\b", str(title), re.I)
            for title in [*target_titles, *preferred_roles]
        )
    )
    jobs, removed_by_filters = job_search.filter_jobs(
        result["jobs"],
        experience_level=experience_level or "any",
        location=str(preferences["location"]),
        employment_type=str(preferences["employment_type"]),
        minimum_salary=preferences["minimum_salary"],
        career_category=str(preferences["career_category"]),
        work_mode=str(preferences["work_mode"]),
        allow_senior=requested_senior,
    )
    skill_catalog = _job_skill_catalog()
    jobs = [
        job_search.score_job(
            job,
            target_titles=target_titles,
            profile_skills=profile_skills,
            skill_catalog=skill_catalog,
            education=str(profile["education"]),
            work_experience=str(profile["work_experience"]),
            experience_level=experience_level or "any",
            location=str(preferences["location"]),
            employment_type=str(preferences["employment_type"]),
            minimum_salary=preferences["minimum_salary"],
            career_category=str(preferences["career_category"]),
            work_mode=str(preferences["work_mode"]),
        )
        for job in jobs
    ]
    job_search.sort_ranked_jobs(jobs, sort_by)

    total = len(jobs)
    start = (page - 1) * per_page
    jobs = jobs[start:start + per_page]
    has_direct_match = any(not job["is_alternative"] for job in jobs)
    alternative_titles: list[str] = []
    if not has_direct_match:
        seen_alternatives: set[str] = set()
        for title in target_titles:
            for related in job_search.related_role_terms(title):
                key = related.casefold()
                if key != title.casefold() and key not in seen_alternatives:
                    alternative_titles.append(related)
                    seen_alternatives.add(key)
        alternative_titles = alternative_titles[:6]

    if (
        os.environ.get("APP_ENV", "").casefold() == "development"
        or os.environ.get("DEBUG", "").casefold() == "true"
    ):
        _LOGGER.info(
            "job-search filtering retrieved=%d removed=%d recommendations=%d",
            len(result["jobs"]),
            removed_by_filters,
            len(jobs),
        )

    return {
        "jobs": jobs,
        "total": total,
        "total_is_estimate": False,
        "page": page,
        "per_page": per_page,
        "page_count": (total + per_page - 1) // per_page,
        "query": query.strip(),
        "preferred_roles": preferred_roles,
        "search_roles": provider_roles,
        "location": preferences["location"],
        "employment_type": preferences["employment_type"],
        "career_category": preferences["career_category"],
        "work_mode": preferences["work_mode"],
        "experience_level": experience_level or "unknown",
        "search_queries": search_queries,
        "provider_warnings": result["failed_queries"],
        "providers": result["providers"],
        "alternative_role_suggestions": alternative_titles,
        "has_direct_role_match": has_direct_match,
        "cached": cached,
        "source": "Public job boards",
        "source_url": "",
    }
