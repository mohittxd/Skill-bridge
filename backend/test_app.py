"""Run with: python -m unittest -v test_app.

Every test uses a temporary database; your administrator and employee data are
never changed. Passwords are generated in memory, not shipped as demo accounts.
"""

from unittest.mock import AsyncMock
import copy
import csv
import hashlib
import io
import json
import os
import secrets
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.error import URLError
from unittest.mock import patch

from fastapi.testclient import TestClient
import numpy as np
from pypdf import PdfWriter

import career_recommendations
import main
import download_esco_api
import import_esco
import import_onet
import job_search

import setup_admin


ORIGIN = main.FRONTEND_ORIGIN
SAMPLES = Path(__file__).parent / "sample_resumes"
EDITABLE_FIELDS = (
    "full_name", "email", "phone", "education", "work_experience", "skills"
)


class FixtureSentenceEncoder:
    def encode(self, texts, **_kwargs):
        vectors = []
        for text in texts:
            normalized = text.casefold()
            if any(term in normalized for term in (
                "figma", "wireframe", "prototype", "user interface", "ux designer",
            )):
                vector = [0.0, 0.0, 1.0, 0.0]
            elif any(term in normalized for term in (
                "java", "spring boot", "software developer", "backend developer",
                "programming",
            )):
                vector = [0.0, 1.0, 0.0, 0.0]
            elif any(term in normalized for term in (
                "data analyst", "data analysis", "business intelligence",
                "reporting analyst", "python", "power bi", "excel",
            )):
                vector = [1.0, 0.0, 0.0, 0.0]
            else:
                vector = [0.0, 0.0, 0.0, 1.0]
            vectors.append(vector)
        return np.asarray(vectors, dtype=np.float32)


class EmployeeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-gap-test-")
        self.path_patch = patch.object(
            main, "DATABASE_PATH", Path(self.temp.name) / "test.db"
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.temp.cleanup)
        main._failed_logins.clear()
        self.client = TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.password = secrets.token_urlsafe(20)
        with patch("builtins.input", return_value="hr_demo"), patch(
            "getpass.getpass", return_value=self.password
        ), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(setup_admin.main(), 0)

    def register(self, username="employee_a", client=None):
        client = client or self.client
        response = client.post("/api/auth/register", json={
            "full_name": "Maya Shah", "username": username, "password": self.password
        })
        self.assertIn(response.status_code, (200, 201), response.text)
        return response

    def login(self, username="hr_demo", client=None):
        response = (client or self.client).post("/api/auth/login", json={
            "username": username, "password": self.password
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response

    def draft(self):
        profile = self.client.get("/api/profile").json()["profile"]
        return {key: profile[key] for key in EDITABLE_FIELDS}

    def upload(self, name="maya_shah.docx", client=None):
        return (client or self.client).post("/api/resume/parse", files={
            "file": (name, (SAMPLES / name).read_bytes())
        })

    def test_registration_login_refresh_logout_and_cookie_security(self):
        registration = self.register()
        user = registration.json()["user"]
        self.assertEqual(user["role"], "employee")
        cookie = registration.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        token = self.client.cookies.get(main.COOKIE_NAME)
        with main.database_connection() as connection:
            hashes = [row[0] for row in connection.execute("SELECT token_hash FROM sessions")]
        self.assertIn(hashlib.sha256(token.encode()).hexdigest(), hashes)
        self.assertNotIn(token, hashes)
        for _ in range(2):
            self.assertEqual(self.client.get("/api/auth/me").json()["user"], user)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.client.cookies.set(main.COOKIE_NAME, token)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.client.cookies.clear()
        self.assertEqual(self.login("employee_a").json()["user"]["role"], "employee")

    def test_unique_username_and_no_role_escalation(self):
        self.register()
        for username in ("EMPLOYEE_A", "hr_demo", "HR_DEMO"):
            response = self.client.post("/api/auth/register", json={
                "full_name": "Other", "username": username, "password": self.password
            })
            self.assertEqual(response.status_code, 409, response.text)
        response = self.client.post("/api/auth/register", json={
            "full_name": "Other", "username": "attacker", "password": self.password,
            "role": "admin"
        })
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get("/api/auth/me").json()["user"]["role"], "employee")

    def test_unauthenticated_and_hr_only_routes(self):
        protected = ["/api/profile", "/api/skills", "/api/roles", "/api/employees",
                     "/api/career-recommendations", "/api/matches",
                     "/api/my-analysis?role_id=data-analyst",
                     "/api/analysis?employee_id=UNKNOWN&role_id=unknown"]
        for route in protected:
            self.assertEqual(self.client.get(route).status_code, 401, route)
        self.assertEqual(self.upload().status_code, 401)
        self.register()
        for route in ["/api/employees", "/api/analysis?employee_id=EMP001&role_id=data-analyst",
                      "/api/analysis?employee_id=UNKNOWN&role_id=unknown"]:
            self.assertEqual(self.client.get(route).status_code, 403, route)
        self.assertEqual(self.client.get("/api/roles").status_code, 200)

    def test_career_recommendations_explain_missing_esco_setup(self):
        self.register()
        saved = self.client.put("/api/profile", json=self.draft())
        self.assertEqual(saved.status_code, 200, saved.text)
        response = self.client.get("/api/career-recommendations")
        self.assertEqual(response.status_code, 503)
        self.assertIn("ESCO", response.json()["detail"])
        self.assertIn("import_esco.py", response.json()["detail"])
        compatibility_response = self.client.get(
            "/api/matches?q=&category=&page=1&page_size=10"
        )
        self.assertEqual(compatibility_response.status_code, 503)
        self.assertIn("ESCO", compatibility_response.json()["detail"])

    def test_upload_review_edit_save_refresh_and_analysis(self):
        self.register()
        for filename in ("maya_shah.pdf", "maya_shah.docx"):
            parsed = self.upload(filename)
            self.assertEqual(parsed.status_code, 200, parsed.text)
            profile = parsed.json()["profile"]
            self.assertEqual(profile["full_name"], "Maya Shah")
            self.assertTrue(profile["email"])
            self.assertTrue(profile["phone"])
            self.assertTrue(profile["education"])
            self.assertTrue(profile["work_experience"])
            skill_names = {skill["name"] for skill in profile["skills"]}
            self.assertTrue({"SQL", "Python", "Git", "JavaScript"}.issubset(skill_names), skill_names)
            self.assertNotIn("Science", skill_names)
            self.assertTrue(all(skill["level"] is None for skill in profile["skills"]))
        self.assertFalse(self.client.get("/api/profile").json()["saved"])
        profile["full_name"] = "Maya Shah (corrected)"
        profile["email"] = "maya.corrected@example.test"
        profile["skills"] = [
            {"name": "SQL", "level": 0, "source": "resume"},
            {"name": "Python", "level": 3, "source": "resume"},
            {"name": "Communication", "level": None, "source": "manual"}
        ]
        saved = self.client.put("/api/profile", json=profile)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["saved"])
        self.client.post("/api/auth/logout")
        self.login("employee_a")
        refreshed = self.draft()
        self.assertEqual(refreshed, profile)
        response = self.client.get("/api/my-analysis?role_id=data-analyst")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        skills = {skill["skill"]: skill for skill in result["skills"]}
        self.assertEqual(skills["SQL"]["current_level"], 0)
        self.assertTrue(skills["SQL"]["assessed"])
        self.assertEqual(skills["SQL"]["rating_source"], "Self-reported")
        self.assertEqual(skills["Statistics"]["current_label"], "Unassessed")
        self.assertFalse(skills["Statistics"]["assessed"])
        self.assertEqual(result["summary"]["skills_met_percentage"], 20)
        self.assertEqual(result["summary"]["skills_with_gaps"], 4)
        self.assertTrue(result["recommendations"])
        changed = self.client.get("/api/my-analysis?role_id=qa-engineer").json()
        self.assertNotEqual(result["skills"], changed["skills"])

    def test_replacement_does_not_overwrite_until_save(self):
        self.register()
        profile = self.draft()
        profile["full_name"] = "My confirmed name"
        profile["skills"] = [{"name": "Python", "level": 5, "source": "manual"}]
        self.assertEqual(self.client.put("/api/profile", json=profile).status_code, 200)
        self.assertEqual(self.upload().status_code, 200)
        self.assertEqual(self.draft(), profile)

    def test_employees_cannot_read_or_overwrite_each_other(self):
        first = self.register().json()["user"]
        profile = self.draft()
        profile["email"] = "private-a@example.test"
        self.client.put("/api/profile", json=profile)
        with TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
            second = self.register("employee_b", client=other).json()["user"]
            self.assertNotEqual(first["id"], second["id"])
            response = other.get(f'/api/profile?employee_id={first["id"]}')
            self.assertNotIn("private-a@example.test", response.text)
            self.assertIn(response.status_code, (200, 400, 422))
            response = other.get(f'/api/my-analysis?role_id=data-analyst&employee_id={first["id"]}')
            if response.status_code == 200:
                self.assertNotEqual(str(response.json()["employee"]["id"]), str(first["id"]))
            else:
                self.assertIn(response.status_code, (400, 422))
            malicious = copy.deepcopy(profile)
            malicious["employee_id"] = first["id"]
            self.assertEqual(other.put("/api/profile", json=malicious).status_code, 422)
            self.assertEqual(other.get(f'/api/profile/{first["id"]}').status_code, 404)
        self.assertEqual(self.draft()["email"], "private-a@example.test")

    def test_invalid_uploads_do_not_change_profile(self):
        self.register()
        original = self.draft()
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        scanned = io.BytesIO()
        writer.write(scanned)
        cases = [
            ("resume.txt", b"resume", 415),
            ("resume.pdf", b"not a pdf", 400),
            ("resume.docx", b"not a docx", 400),
            ("empty.pdf", scanned.getvalue(), 400),
            ("large.pdf", b"%PDF-" + b"x" * (5 * 1024 * 1024), 413),
        ]
        for filename, data, expected in cases:
            response = self.client.post("/api/resume/parse", files={"file": (filename, data)})
            self.assertEqual(response.status_code, expected, response.text)
            self.assertIsInstance(response.json()["detail"], str)
            self.assertEqual(self.draft(), original)

    def test_ratings_are_strict_and_unrated_is_not_zero(self):
        self.register()
        profile = self.draft()
        for level in (-1, 6, 2.5, True, "3"):
            profile["skills"] = [{"name": "SQL", "level": level, "source": "manual"}]
            self.assertEqual(self.client.put("/api/profile", json=profile).status_code, 422, level)
        profile["skills"] = [{"name": "SQL", "level": None, "source": "resume"}]
        self.assertEqual(self.client.put("/api/profile", json=profile).status_code, 200)
        result = self.client.get("/api/my-analysis?role_id=data-analyst").json()
        sql = next(skill for skill in result["skills"] if skill["skill"] == "SQL")
        self.assertFalse(sql["assessed"])
        self.assertEqual(sql["gap"], 4)
        self.assertEqual(sql["current_label"], "Unassessed")

    def test_origin_invalid_credentials_and_expiry(self):
        self.register()
        for method, route in (("post", "/api/auth/logout"), ("put", "/api/profile"),
                              ("post", "/api/resume/parse")):
            options = {"headers": {"Origin": "http://other.example"}}
            if route == "/api/profile":
                options["json"] = self.draft()
            elif route == "/api/resume/parse":
                options["files"] = {"file": ("resume.pdf", b"bad")}
            self.assertEqual(getattr(self.client, method)(route, **options).status_code, 403)
        self.assertEqual(self.client.post("/api/auth/login", json={
            "username": "employee_a", "password": "not-the-password"
        }).status_code, 401)
        with main.database_connection() as connection:
            connection.execute("UPDATE sessions SET expires_at = ?", (int(time.time()) - 1,))
            connection.commit()
        self.assertEqual(self.client.get("/api/profile").status_code, 401)
        self.assertEqual(self.client.get("/api/my-analysis?role_id=data-analyst").status_code, 401)

    def test_existing_hr_workflow_and_setup(self):
        self.assertEqual(self.login().json()["user"]["role"], "admin")
        self.assertGreaterEqual(len(self.client.get("/api/employees").json()["employees"]), 5)
        self.assertEqual(len(self.client.get("/api/roles").json()["roles"]), 3)
        aisha = self.client.get("/api/analysis?employee_id=EMP001&role_id=data-analyst").json()
        self.assertEqual(aisha["summary"]["skills_met_percentage"], 20)
        self.assertTrue(aisha["recommendations"])
        kabir = self.client.get("/api/analysis?employee_id=EMP005&role_id=backend-developer").json()
        self.assertTrue(any(not group["courses"] for group in kabir["recommendations"]))
        vikram = self.client.get("/api/analysis?employee_id=EMP004&role_id=data-analyst").json()
        self.assertEqual(vikram["summary"]["skills_met_percentage"], 100)
        self.assertEqual(vikram["recommendations"], [])
        self.assertEqual(self.client.get("/api/analysis?employee_id=UNKNOWN&role_id=data-analyst").status_code, 404)
        with patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(setup_admin.main(), 1)


class MatchingWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skill-gap-onet-")
        self.database = Path(self.temp.name) / "matching.db"
        self.source_dir = Path(self.temp.name) / "onet-source"
        self.source_dir.mkdir()
        self._write_onet_sources()
        self.import_counts = import_onet.import_catalog(self.database, self.source_dir)
        self.path_patch = patch.object(main, "DATABASE_PATH", self.database)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.addCleanup(self.temp.cleanup)
        self.password = secrets.token_urlsafe(20)
        main._failed_logins.clear()
        self.client = TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        with patch("builtins.input", return_value="hr_demo"), patch(
            "getpass.getpass", return_value=self.password
        ), patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(setup_admin.main(), 0)

    def _write_csv(self, filename, headers, rows):
        with (self.source_dir / filename).open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def _write_onet_sources(self):
        occupation_headers = ["O*NET-SOC Code", "Title", "Description"]
        occupations = [
            ("15-2051.00", "Data Scientists", "Analyze data and develop quantitative models."),
            ("15-1253.00", "Software Quality Assurance Analysts and Testers", "Test software products."),
            ("15-1252.00", "Software Developers", "Create and maintain computer applications."),
            ("29-1141.00", "Registered Nurses", "Provide and coordinate patient care."),
        ]
        self._write_csv("occupation_data.csv", occupation_headers, [
            dict(zip(occupation_headers, occupation)) for occupation in occupations
        ])

        rating_headers = [
            "O*NET-SOC Code", "Title", "Element ID", "Element Name", "Scale ID",
            "Scale Name", "Data Value", "N", "Standard Error", "Lower CI Bound",
            "Upper CI Bound", "Recommend Suppress", "Not Relevant", "Date", "Domain Source",
        ]
        essential = []
        for code, title, _ in occupations:
            for scale, scale_name, value in (
                ("IM", "Importance", 1.0 if code == "29-1141.00" else 1.5),
                ("LV", "Level", 5.0 if code == "29-1141.00" else 4.2),
            ):
                essential.append({
                    "O*NET-SOC Code": code,
                    "Title": title,
                    "Element ID": "2.A.1.b",
                    "Element Name": "Active Listening",
                    "Scale ID": scale,
                    "Scale Name": scale_name,
                    "Data Value": str(value),
                    "N": "10",
                    "Standard Error": "",
                    "Lower CI Bound": "",
                    "Upper CI Bound": "",
                    "Recommend Suppress": "N",
                    "Not Relevant": "",
                    "Date": "08/2023",
                    "Domain Source": "Analyst",
                })
        self._write_csv("essential_skills.csv", rating_headers, essential)

        transferable = []
        for code, title, _ in occupations:
            if code not in {"15-2051.00", "15-1252.00"}:
                continue
            for scale, scale_name, value in (("IM", "Importance", 4.0), ("LV", "Level", 5.0)):
                transferable.append({
                    "O*NET-SOC Code": code,
                    "Title": title,
                    "Element ID": "2.B.1.a",
                    "Element Name": "Programming",
                    "Scale ID": scale,
                    "Scale Name": scale_name,
                    "Data Value": str(value),
                    "N": "10",
                    "Standard Error": "",
                    "Lower CI Bound": "",
                    "Upper CI Bound": "",
                    "Recommend Suppress": "N",
                    "Not Relevant": "",
                    "Date": "08/2023",
                    "Domain Source": "Analyst",
                })
        self._write_csv("transferable_skills.csv", rating_headers, transferable)

        software_headers = [
            "O*NET-SOC Code", "Title", "Workplace Example", "Element ID",
            "Element Name", "Hot Technology", "In Demand",
        ]
        examples = {
            "15-2051.00": ["Python", "Microsoft SQL Server", "Tableau"],
            "15-1253.00": ["Selenium", "Atlassian JIRA", "Microsoft SQL Server"],
            "15-1252.00": ["Python", "JavaScript", "Git", "Microsoft SQL Server"],
            "29-1141.00": ["Microsoft Excel"],
        }
        software = []
        for code, title, _ in occupations:
            for example in examples.get(code, []):
                software.append({
                    "O*NET-SOC Code": code,
                    "Title": title,
                    "Workplace Example": example,
                    "Element ID": "2.E.1",
                    "Element Name": "Software",
                    "Hot Technology": "Y",
                    "In Demand": "Y",
                })
        self._write_csv("software_skills.csv", software_headers, software)

    def _save_parsed_resume(self, client, filename, data, ratings):
        response = client.post("/api/resume/parse", files={"file": (filename, data)})
        self.assertEqual(response.status_code, 200, response.text)
        profile = response.json()["profile"]
        confirmed_names = {skill["name"].casefold() for skill in profile["skills"]}
        profile["skills"] = [
            {
                **skill,
                "level": ratings.get(skill["name"].casefold()),
            }
            for skill in profile["skills"]
        ]
        for name, level in ratings.items():
            if name not in confirmed_names:
                profile["skills"].append({
                    "name": name.title(),
                    "level": level,
                    "source": "manual",
                })
        saved = client.put("/api/profile", json=profile)
        self.assertEqual(saved.status_code, 200, saved.text)
        return profile

    def test_import_ranking_search_filters_pages_and_gap_details(self):
        self.assertEqual(self.import_counts["occupations"], 4)
        self.assertEqual(self.import_counts["skills"], 17)
        registered = self.client.post("/api/auth/register", json={
            "full_name": "Maya Shah",
            "username": "maya_onet",
            "password": self.password,
        })
        self.assertEqual(registered.status_code, 201, registered.text)
        self._save_parsed_resume(
            self.client,
            "maya_shah.docx",
            (SAMPLES / "maya_shah.docx").read_bytes(),
            {"python": 3, "sql": 2, "active listening": 2},
        )
        self.assertEqual(import_onet.import_catalog(self.database, self.source_dir), self.import_counts)
        with main.database_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM admins").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM employees").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM onet_occupations").fetchone()[0], 4)

        first_page = self.client.get("/api/matching/roles?limit=1&offset=0")
        second_page = self.client.get("/api/matching/roles?limit=1&offset=1")
        self.assertEqual(first_page.status_code, 200, first_page.text)
        self.assertEqual(first_page.json()["considered"], 4)
        self.assertEqual(len(first_page.json()["roles"]), 1)
        self.assertNotEqual(first_page.json()["roles"][0]["id"], second_page.json()["roles"][0]["id"])
        self.assertEqual(first_page.json()["roles"][0]["id"], "15-1252.00")
        self.assertEqual(
            self.client.get("/api/matching/roles?query=Data%20Scientists").json()["total"],
            1,
        )
        category = self.client.get("/api/matching/roles?category_code=29").json()
        self.assertEqual(category["total"], 1)
        self.assertEqual(category["roles"][0]["title"], "Registered Nurses")
        empty = self.client.get("/api/matching/roles?query=no-such-occupation").json()
        self.assertEqual(empty["roles"], [])
        self.assertEqual(empty["total"], 0)

        detail = self.client.get("/api/matching/roles/15-2051.00")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertIn("O*NET Importance values weight rated skills", detail.json()["skill_match"]["explanation"])
        active_listening = next(
            skill for skill in detail.json()["skills"] if skill["skill"] == "Active Listening"
        )
        self.assertEqual(active_listening["gap"], 1.0)
        self.assertEqual(active_listening["rating_source"], "Self-reported")
        self.assertIsNone(next(
            skill for skill in detail.json()["skills"] if skill["skill"] == "SQL"
        )["gap"])
        visualization = next(
            item for item in detail.json()["recommendations"]
            if item["skill"] == "Data Visualization"
        )
        self.assertTrue(visualization["courses"])
        no_course = next(
            item for item in detail.json()["recommendations"]
            if item["skill"] == "Active Listening"
        )
        self.assertEqual(no_course["courses"], [])

    def test_two_resume_skill_sets_and_employee_isolation(self):
        first = self.client.post("/api/auth/register", json={
            "full_name": "Maya Shah",
            "username": "maya_match",
            "password": self.password,
        })
        self.assertEqual(first.status_code, 201, first.text)
        self._save_parsed_resume(
            self.client,
            "maya_shah.docx",
            (SAMPLES / "maya_shah.docx").read_bytes(),
            {"python": 4, "sql": 4, "git": 4, "javascript": 4},
        )
        first_id = first.json()["user"]["id"]
        first_top = self.client.get("/api/matching/roles").json()["roles"][0]
        self.assertEqual(first_top["id"], "15-1252.00")
        self.assertIn("SQL", first_top["matched_skills"])

        from docx import Document

        second_resume = io.BytesIO()
        document = Document()
        document.add_heading("Jordan Lee", level=0)
        document.add_heading("Skills", level=1)
        document.add_paragraph("Selenium, Jira, SQL")
        document.save(second_resume)
        with TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as other:
            second = other.post("/api/auth/register", json={
                "full_name": "Jordan Lee",
                "username": "jordan_match",
                "password": self.password,
            })
            self.assertEqual(second.status_code, 201, second.text)
            self._save_parsed_resume(
                other,
                "jordan_lee.docx",
                second_resume.getvalue(),
                {"test automation": 4, "bug tracking": 4, "sql": 3},
            )
            second_roles = other.get(
                f"/api/matching/roles?employee_id={first_id}"
            ).json()["roles"]
            self.assertEqual(second_roles[0]["id"], "15-1253.00")
            self.assertNotEqual(second_roles[0]["id"], first_top["id"])
            second_detail = other.get("/api/matching/roles/15-1253.00").json()
            self.assertEqual(second_detail["employee"]["id"], second.json()["user"]["id"])
            self.assertNotEqual(second_detail["employee"]["id"], first_id)
            self.assertIn("Bug Tracking", second_detail["skill_match"]["matched_skills"])

        with TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as admin:
            response = admin.post("/api/auth/login", json={
                "username": "hr_demo", "password": self.password
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(admin.get("/api/matching/roles").status_code, 403)
            self.assertEqual(
                admin.get("/api/matching/roles/15-1253.00").status_code,
                403,
            )
            self.assertEqual(admin.get("/api/career-recommendations").status_code, 403)
            self.assertEqual(admin.get("/api/matches").status_code, 403)

    def _register_job_search_employee(self):
        response = self.client.post("/api/auth/register", json={
            "full_name": "Ari Patel",
            "username": "ari_jobs",
            "password": self.password,
        })
        self.assertEqual(response.status_code, 201, response.text)
        profile = {
            "full_name": "Ari Patel",
            "email": "ari@example.test",
            "phone": "",
            "education": "BSc Computer Science",
            "work_experience": "Two years of data reporting experience.",
            "skills": [
                {"name": "Python", "level": 4, "source": "manual"},
                {"name": "SQL", "level": 3, "source": "manual"},
            ],
        }
        saved = self.client.put("/api/profile", json=profile)
        self.assertEqual(saved.status_code, 200, saved.text)
        preferences = self.client.put("/api/job-preferences", json={
            "preferred_roles": ["Data Analyst", " Data Analyst "],
            "location": "Bengaluru",
            "experience_level": "entry",
            "employment_type": "permanent",
            "minimum_salary": 600000,
        })
        self.assertEqual(preferences.status_code, 200, preferences.text)
        return profile

    def _create_search_profile(self, client, username, role, skills, location):
        registration = client.post("/api/auth/register", json={
            "full_name": f"{username} Employee",
            "username": username,
            "password": self.password,
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        profile = {
            "full_name": f"{username} Employee",
            "email": "",
            "phone": "",
            "education": "Bachelor of Computer Science",
            "work_experience": "Fresher",
            "skills": [
                {"name": name, "level": None, "source": "resume"}
                for name in skills
            ],
        }
        saved_profile = client.put("/api/profile", json=profile)
        self.assertEqual(saved_profile.status_code, 200, saved_profile.text)
        saved_preferences = client.put("/api/job-preferences", json={
            "preferred_roles": [role] if role else [],
            "location": location,
            "experience_level": "entry",
            "employment_type": "any",
            "minimum_salary": None,
            "career_category": "",
            "work_mode": "any",
        })
        self.assertEqual(saved_preferences.status_code, 200, saved_preferences.text)

    def _import_esco_test_catalog(self):
        source_dir = self.source_dir / "esco"
        source_dir.mkdir()
        occupations = [
            {
                "conceptUri": "https://example.test/esco/data-analyst",
                "preferredLabel": "Data analyst",
                "altLabels": "Business intelligence analyst|Reporting analyst",
                "description": "Analyse data and prepare reports for business decisions.",
                "iscoGroup": "2511",
            },
            {
                "conceptUri": "https://example.test/esco/software-developer",
                "preferredLabel": "Software developer",
                "altLabels": "Java developer|Backend developer",
                "description": "Develop and maintain software applications.",
                "iscoGroup": "2512",
            },
            {
                "conceptUri": "https://example.test/esco/interface-designer",
                "preferredLabel": "User interface designer",
                "altLabels": "UX designer|Product designer",
                "description": "Design user interfaces and test interactive prototypes.",
                "iscoGroup": "2166",
            },
        ]
        skills = [
            ("python", "Python", "Python programming"),
            ("sql", "SQL", "Structured query language"),
            ("analysis", "Data analysis", "data analytics"),
            ("excel", "Excel", "Microsoft Excel"),
            ("powerbi", "Power BI", "Microsoft Power BI"),
            ("java", "Java", "Java programming"),
            ("spring", "Spring Boot", "Spring framework"),
            ("programming", "Programming", "software programming"),
            ("figma", "Figma", "Figma design software"),
            ("wireframe", "Wireframing", "wireframes"),
            ("prototype", "Prototyping", "interactive prototype design"),
        ]
        skill_rows = [
            {
                "conceptUri": f"https://example.test/esco/skill/{key}",
                "preferredLabel": label,
                "altLabels": aliases,
                "description": f"ESCO knowledge and skill concept for {label}.",
                "skillType": "skill/competence",
            }
            for key, label, aliases in skills
        ]
        skill_uri = {
            row["preferredLabel"]: row["conceptUri"]
            for row in skill_rows
        }
        relations = []
        for occupation, required, optional in (
            (occupations[0], ["Python", "SQL", "Data analysis"], ["Excel", "Power BI"]),
            (occupations[1], ["Java", "Spring Boot", "Programming"], ["SQL"]),
            (occupations[2], ["Figma", "Wireframing"], ["Prototyping"]),
        ):
            for relation_type, labels in (("essential", required), ("optional", optional)):
                for label in labels:
                    relations.append({
                        "occupationUri": occupation["conceptUri"],
                        "relationType": relation_type,
                        "skillType": "skill/competence",
                        "skillUri": skill_uri[label],
                    })
        for filename, rows in (
            ("occupations_en.csv", occupations),
            ("skills_en.csv", skill_rows),
            ("occupationSkillRelations_en.csv", relations),
        ):
            with (source_dir / filename).open("w", encoding="utf-8", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        return import_esco.import_catalog(
            self.database,
            source_dir,
            version="test",
            model=FixtureSentenceEncoder(),
        )

    def test_official_api_export_creates_importable_esco_csvs(self):
        destination = self.source_dir / "esco-api"
        occupation_uri = "https://example.test/esco/occupation/data"
        python_uri = "https://example.test/esco/skill/python"
        sql_uri = "https://example.test/esco/skill/sql"
        occupation = {
            "uri": occupation_uri,
            "title": "Data analyst",
            "code": "2511.2",
            "preferredLabel": {"en": "Data analyst"},
            "alternativeLabel": {"en": ["Reporting analyst"]},
            "description": {"en": {"literal": "Analyse and report data."}},
            "_links": {
                "hasEssentialSkill": [{
                    "uri": python_uri,
                    "skillType": "http://data.europa.eu/esco/skill-type/skill",
                }],
                "hasOptionalSkill": [{
                    "uri": sql_uri,
                    "skillType": "http://data.europa.eu/esco/skill-type/knowledge",
                }],
            },
        }
        skills = {
            "https://example.test/esco/skill/python": {
                "uri": python_uri,
                "title": "Python",
                "preferredLabel": {"en": "Python"},
                "alternativeLabel": {"en": ["Python programming"]},
                "description": {"en": {"literal": "Use Python."}},
            },
            "https://example.test/esco/skill/sql": {
                "uri": sql_uri,
                "title": "SQL",
                "preferredLabel": {"en": "SQL"},
                "alternativeLabel": {"en": ["Structured query language"]},
                "description": {"en": {"literal": "Use SQL."}},
            },
        }
        with patch(
            "download_esco_api._collection_pages",
            side_effect=[
                (1, iter([{"_embedded": {occupation_uri: occupation}}])),
                (2, iter([{"_embedded": skills}])),
            ],
        ):
            exported = download_esco_api.export_catalog(
                destination,
                version="1.2.1",
                occupation_page_size=1,
                skill_page_size=1,
                workers=1,
            )
        self.assertEqual(exported, {
            "occupations": 1,
            "skills": 2,
            "relations": 2,
        })
        imported = import_esco.import_catalog(
            self.database,
            destination,
            version="1.2.1",
            model=FixtureSentenceEncoder(),
        )
        self.assertEqual(imported["occupations"], 1)
        self.assertEqual(imported["skills"], 2)
        self.assertEqual(imported["relations"], 2)

    def test_automatic_career_recommendations_are_independent_and_personalized(self):
        counts = self._import_esco_test_catalog()
        self.assertEqual(counts["occupations"], 3)
        self.assertGreater(counts["computed"], 0)
        refreshed = import_esco.import_catalog(
            self.database,
            self.source_dir / "esco",
            version="test",
            model=FixtureSentenceEncoder(),
        )
        self.assertEqual(refreshed["computed"], 0)
        with main.database_connection() as connection:
            onet_count = connection.execute(
                "SELECT COUNT(*) FROM onet_occupations"
            ).fetchone()[0]
        self.assertEqual(onet_count, self.import_counts["occupations"])
        clients = [self.client]
        extra_clients = [
            TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}),
            TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}),
        ]
        for client in extra_clients:
            client.__enter__()
            self.addCleanup(client.__exit__, None, None, None)
            clients.append(client)
        profiles = (
            ("career_data", "", ["Python", "SQL", "Excel", "Power BI"], "Bengaluru"),
            ("career_java", "", ["Java", "Spring Boot", "SQL"], "Pune"),
            ("career_design", "", ["Figma", "Wireframing", "Prototyping"], "Mumbai"),
        )
        for client, (username, role, skills, location) in zip(clients, profiles):
            self._create_search_profile(client, username, role, skills, location)

        employee_ids = [client.get("/api/auth/me").json()["user"]["id"] for client in clients]
        with patch("career_recommendations._load_model", return_value=FixtureSentenceEncoder()), \
             patch(
                 "job_search.public_job_providers.fetch_public_listings",
                 side_effect=AssertionError("Career matching must not call vacancy providers"),
             ):
            responses = [client.get("/api/career-recommendations") for client in clients]
            forged_employee_response = clients[0].get(
                f"/api/career-recommendations?employee_id={employee_ids[1]}"
            )
            alternate_title = clients[0].get(
                "/api/career-recommendations?query=Reporting+analyst"
            )
            related_skill = clients[0].get(
                "/api/career-recommendations?query=Power+BI"
            )
            empty_result = clients[0].get(
                "/api/career-recommendations?query=astronaut"
            )
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["roles"])
            self.assertEqual(response.json()["considered"], 3)
            self.assertEqual(response.json()["source"], "ESCO")
        data, java, design = [response.json()["roles"] for response in responses]
        self.assertEqual(data[0]["title"], "Data analyst")
        self.assertEqual(java[0]["title"], "Software developer")
        self.assertEqual(design[0]["title"], "User interface designer")
        self.assertEqual(forged_employee_response.status_code, 200)
        self.assertEqual(forged_employee_response.json()["roles"], data)
        for role in (data[0], java[0], design[0]):
            self.assertTrue(0 <= role["compatibility_score"] <= 100)
            self.assertTrue(role["required_skills"])
            self.assertTrue(role["reason"])
        self.assertEqual(data[0]["optional_skills"], ["Excel", "Power BI"])

        self.assertEqual(alternate_title.status_code, 200, alternate_title.text)
        self.assertEqual(alternate_title.json()["roles"][0]["title"], "Data analyst")
        self.assertEqual(related_skill.status_code, 200, related_skill.text)
        self.assertEqual(related_skill.json()["roles"][0]["title"], "Data analyst")
        self.assertEqual(empty_result.status_code, 200, empty_result.text)
        self.assertEqual(empty_result.json()["roles"], [])
        self.assertTrue(empty_result.json()["message"])

        updated_preferences = clients[2].get("/api/job-preferences").json()["preferences"]
        updated_preferences.pop("updated_at", None)
        updated_preferences["preferred_roles"] = ["UX designer"]
        saved_preferences = clients[2].put(
            "/api/job-preferences", json=updated_preferences
        )
        self.assertEqual(saved_preferences.status_code, 200, saved_preferences.text)
        with patch("career_recommendations._load_model", return_value=FixtureSentenceEncoder()):
            preference_response = clients[2].get("/api/career-recommendations")
        self.assertEqual(preference_response.status_code, 200, preference_response.text)
        preferred_design = next(
            role for role in preference_response.json()["roles"]
            if role["title"] == "User interface designer"
        )
        self.assertGreater(preferred_design["preference_match"], 0)

        with patch("career_recommendations._load_model", return_value=FixtureSentenceEncoder()):
            compatibility_auto = clients[0].get(
                "/api/matches?q=&category=&page=1&page_size=10"
            )
            compatibility_data = clients[0].get(
                "/api/matches?q=data+analyst&category=&page=1&page_size=10"
            )
            canonical_category = clients[0].get(
                "/api/career-recommendations?category=2511"
            )
            compatibility_software = clients[1].get(
                "/api/matches?q=software&category=&page=1&page_size=10"
            )
            compatibility_developer = clients[1].get(
                "/api/matches?q=developer&category=&page=1&page_size=10"
            )
            compatibility_category = clients[2].get(
                "/api/matches?q=&category=2166&page=1&page_size=10"
            )
            compatibility_page = clients[0].get(
                "/api/matches?q=&category=&page=2&page_size=1"
            )
        for response in (
            compatibility_auto,
            compatibility_data,
            canonical_category,
            compatibility_software,
            compatibility_developer,
            compatibility_category,
            compatibility_page,
        ):
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["source"], "ESCO")
        for response in (
            compatibility_auto,
            compatibility_data,
            compatibility_software,
            compatibility_developer,
            compatibility_category,
            compatibility_page,
        ):
            self.assertEqual(response.json()["matches"], response.json()["roles"])
        self.assertEqual(
            compatibility_auto.json()["roles"][0]["title"],
            "Data analyst",
        )
        self.assertEqual(
            compatibility_data.json()["roles"][0]["title"],
            "Data analyst",
        )
        self.assertEqual(
            canonical_category.json()["roles"][0]["title"],
            "Data analyst",
        )
        self.assertIn(
            {"code": "2166", "name": "ISCO 2166"},
            canonical_category.json()["categories"],
        )
        self.assertEqual(
            compatibility_software.json()["roles"][0]["title"],
            "Software developer",
        )
        self.assertEqual(
            compatibility_developer.json()["roles"][0]["title"],
            "Software developer",
        )
        self.assertEqual(
            compatibility_category.json()["roles"][0]["title"],
            "User interface designer",
        )
        self.assertIn(
            {"code": "2511", "name": "ISCO 2511"},
            compatibility_auto.json()["categories"],
        )
        self.assertEqual(compatibility_page.json()["page"], 2)
        self.assertEqual(compatibility_page.json()["total"], 3)
        self.assertEqual(len(compatibility_page.json()["roles"]), 1)

        incomplete = clients[0].get("/api/profile").json()["profile"]
        empty_profile = {
            key: incomplete[key]
            for key in EDITABLE_FIELDS
        }
        empty_profile["education"] = ""
        empty_profile["work_experience"] = ""
        empty_profile["skills"] = []
        saved_empty_profile = clients[0].put("/api/profile", json=empty_profile)
        self.assertEqual(saved_empty_profile.status_code, 200, saved_empty_profile.text)
        with patch("career_recommendations._load_model", return_value=FixtureSentenceEncoder()):
            empty_profile_recommendations = clients[0].get("/api/career-recommendations")
            manual_result = clients[0].get(
                "/api/career-recommendations?query=Data+analyst"
            )
        self.assertEqual(empty_profile_recommendations.status_code, 200)
        self.assertEqual(empty_profile_recommendations.json()["roles"], [])
        self.assertTrue(empty_profile_recommendations.json()["message"])
        self.assertEqual(manual_result.status_code, 200)
        self.assertIsNone(manual_result.json()["roles"][0]["compatibility_score"])


class MigrationTests(unittest.TestCase):
    def test_existing_job_preferences_gain_category_and_work_mode_columns(self):
        with tempfile.TemporaryDirectory(prefix="skill-gap-job-preferences-") as directory:
            database = Path(directory) / "legacy.db"
            with sqlite3.connect(database) as connection:
                connection.execute("""
                    CREATE TABLE employee_job_preferences (
                        employee_id INTEGER PRIMARY KEY,
                        preferred_roles TEXT NOT NULL DEFAULT '[]',
                        location TEXT NOT NULL DEFAULT 'India',
                        experience_level TEXT NOT NULL DEFAULT 'any',
                        employment_type TEXT NOT NULL DEFAULT 'any',
                        minimum_salary INTEGER,
                        updated_at INTEGER NOT NULL
                    )
                """)
                connection.execute(
                    "INSERT INTO employee_job_preferences(employee_id, updated_at) VALUES (1, 123)"
                )
            with patch.object(main, "DATABASE_PATH", database):
                main.initialize_database()
                preferences, saved = main._job_preferences_for_employee(1)
                self.assertTrue(saved)
                self.assertEqual(preferences["career_category"], "")
                self.assertEqual(preferences["work_mode"], "any")

    def test_admin_setup_does_not_duplicate_an_employee_username(self):
        with tempfile.TemporaryDirectory(prefix="skill-gap-setup-") as directory:
            database = Path(directory) / "setup.db"
            with patch.object(main, "DATABASE_PATH", database):
                with TestClient(
                    main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}
                ) as client:
                    response = client.post("/api/auth/register", json={
                        "full_name": "Maya Shah",
                        "username": "shared_name",
                        "password": "secure-example-password",
                    })
                    self.assertEqual(response.status_code, 201, response.text)

                    with patch("builtins.input", return_value="SHARED_NAME"), patch(
                        "getpass.getpass", return_value="secure-example-password"
                    ), patch("sys.stdout", new_callable=io.StringIO):
                        self.assertEqual(setup_admin.main(), 1)

                with main.database_connection() as connection:
                    self.assertEqual(
                        connection.execute("SELECT COUNT(*) FROM admins").fetchone()[0],
                        0,
                    )

    def test_original_admin_and_live_session_survive_idempotent_migration(self):
        with tempfile.TemporaryDirectory(prefix="skill-gap-migration-") as directory:
            database = Path(directory) / "legacy.db"
            password = secrets.token_urlsafe(20)
            token = secrets.token_urlsafe(32)
            now = int(time.time())
            with sqlite3.connect(database) as connection:
                connection.executescript('''
                    CREATE TABLE admins (id INTEGER PRIMARY KEY AUTOINCREMENT,
                        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                        password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
                    CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,
                        admin_id INTEGER NOT NULL, created_at INTEGER NOT NULL,
                        expires_at INTEGER NOT NULL,
                        FOREIGN KEY (admin_id) REFERENCES admins(id) ON DELETE CASCADE);
                    CREATE INDEX idx_sessions_expires_at ON sessions(expires_at);
                ''')
                connection.execute("INSERT INTO admins VALUES (7, ?, ?, ?)",
                                   ("existing_admin", main.PASSWORD_HASHER.hash(password), now))
                connection.execute("INSERT INTO sessions VALUES (?, 7, ?, ?)",
                                   (hashlib.sha256(token.encode()).hexdigest(), now, now + 3600))
            with patch.object(main, "DATABASE_PATH", database):
                main.initialize_database()
                main.initialize_database()
                with TestClient(main.app, base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
                    client.cookies.set(main.COOKIE_NAME, token)
                    response = client.get("/api/auth/me")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["user"]["role"], "admin")
                    self.assertEqual(client.get("/api/employees").status_code, 200)
                    client.post("/api/auth/logout")
                    response = client.post("/api/auth/login", json={
                        "username": "existing_admin", "password": password
                    })
                    self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
