import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

import httpx
from fastapi.testclient import TestClient

import job_search
import main
import public_job_providers


def api_job(
    identifier: str,
    title: str,
    location: str,
    description: str,
    source: str,
    *,
    source_url: str,
    experience_levels=None,
    location_eligible=True,
    work_mode="on_site",
):
    return {
        "id": identifier,
        "title": title,
        "company": "Verified employer",
        "location": location,
        "description": description,
        "salary_text": "",
        "salary_min": None,
        "salary_max": None,
        "salary_currency": "",
        "salary_period": "",
        "salary_is_predicted": False,
        "created": "2026-01-15T12:00:00+00:00",
        "employment_type": "",
        "employment_type_label": "",
        "contract_time": "",
        "category": "Engineering",
        "work_mode": work_mode,
        "application_url": f"https://careers.example.test/jobs/{identifier}",
        "source": source,
        "source_url": source_url,
        "experience_levels": experience_levels or [],
        "location_eligible": location_eligible,
    }


class PublicProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skillbridge-public-jobs-")
        self.addCleanup(self.temp.cleanup)
        self.cache_path = Path(self.temp.name) / "provider-cache.sqlite3"

    def client_factory(self, handler):
        real_client = httpx.AsyncClient

        def create_client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        return create_client

    async def test_three_provider_contracts_normalization_and_cache(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.host == "himalayas.app":
                self.assertEqual(request.method, "GET")
                self.assertEqual(request.url.path, "/jobs/api/search")
                self.assertEqual(parse_qs(request.url.query.decode())["q"], ["Data Analyst"])
                if parse_qs(request.url.query.decode())["page"] == ["1"]:
                    return httpx.Response(200, json={"jobs": [{
                        "guid": "h-1",
                        "title": "Data Analyst",
                        "companyName": "Himalayas Example",
                        "applicationLink": "https://himalayas.app/jobs/h-1",
                        "locationRestrictions": ["India"],
                        "timezoneRestrictions": [5.5],
                        "description": "Python, SQL and Excel reporting.",
                        "employmentType": "Full-time",
                        "currency": "INR",
                        "minSalary": 800000,
                        "maxSalary": 1200000,
                        "salaryPeriod": "yearly",
                        "pubDate": "2026-01-15T12:00:00Z",
                    }]})
                return httpx.Response(200, json={"jobs": [], "totalCount": 1})
            if request.url.host == "boards-api.greenhouse.io":
                self.assertEqual(request.url.path, "/v1/boards/testco/jobs")
                self.assertEqual(parse_qs(request.url.query.decode()), {"content": ["true"]})
                return httpx.Response(200, json={"jobs": [{
                    "id": 22,
                    "title": "Software Engineer Intern",
                    "company_name": "Greenhouse Example",
                    "location": {"name": "Bengaluru, India"},
                    "absolute_url": "https://boards.greenhouse.io/testco/jobs/22",
                    "content": "<p>Python and SQL. Internship, part-time.</p>",
                    "departments": [{"name": "Engineering"}],
                    "first_published": "2026-01-15T12:00:00Z",
                }]})
            self.assertEqual(request.url.host, "api.lever.co")
            self.assertEqual(request.url.path, "/v0/postings/testsite")
            self.assertEqual(parse_qs(request.url.query.decode()), {
                "mode": ["json"], "skip": ["0"], "limit": ["100"],
            })
            return httpx.Response(200, json=[{
                "id": "l-3",
                "text": "UX Design Intern",
                "applyUrl": "https://jobs.lever.co/testsite/l-3/apply",
                "hostedUrl": "https://jobs.lever.co/testsite/l-3",
                "createdAt": 1768478400000,
                "categories": {
                    "location": "Mumbai, India",
                    "commitment": "Internship",
                    "team": "Design",
                },
                "workplaceType": "on-site",
                "descriptionPlain": "Figma, wireframing and prototyping.",
            }])

        with patch.dict(os.environ, {
            "GREENHOUSE_BOARD_TOKENS": "testco",
            "LEVER_SITE_NAMES": "testsite",
        }), patch.object(httpx, "AsyncClient", self.client_factory(handler)):
            result = await public_job_providers.fetch_public_listings(
                ["Data Analyst"], self.cache_path
            )
            self.assertCountEqual(result["providers"], ["Himalayas", "Greenhouse", "Lever"])
            self.assertEqual(len(result["jobs"]), 3)
            self.assertEqual(len(requests), 4)
            cached = await public_job_providers.fetch_public_listings(
                ["Data Analyst"], self.cache_path
            )

        self.assertEqual(len(requests), 4)
        self.assertEqual(cached["cache_hits"], cached["cache_total"])
        jobs = {job["source"]: job for job in result["jobs"]}
        self.assertEqual(jobs["Himalayas"]["location_eligible"], True)
        self.assertEqual(jobs["Himalayas"]["salary_min"], 800000)
        self.assertEqual(jobs["Himalayas"]["application_url"], "https://himalayas.app/jobs/h-1")
        self.assertEqual(jobs["Greenhouse"]["contract_time"], "part_time")
        self.assertEqual(jobs["Greenhouse"]["employment_type"], "internship")
        self.assertEqual(jobs["Lever"]["work_mode"], "on_site")
        self.assertTrue(jobs["Lever"]["application_url"].endswith("/apply"))
        self.assertFalse(result["provider_warnings"])

    async def test_unavailable_provider_does_not_hide_other_results(self):
        def handler(request):
            if request.url.host == "boards-api.greenhouse.io":
                return httpx.Response(429, json={"error": "rate limited"})
            if request.url.host == "himalayas.app":
                return httpx.Response(200, json={"jobs": []})
            self.fail(f"unexpected provider request: {request.url.host}")

        with patch.dict(os.environ, {
            "GREENHOUSE_BOARD_TOKENS": "testco",
            "LEVER_SITE_NAMES": "",
        }), patch.object(httpx, "AsyncClient", self.client_factory(handler)):
            result = await public_job_providers.fetch_public_listings(
                ["Data Analyst"], self.cache_path
            )

        self.assertEqual(result["providers"], ["Himalayas"])
        self.assertEqual(result["provider_warnings"], [{
            "provider": "Greenhouse",
            "error": "The provider rate limit was reached; retry later.",
        }])

    async def test_all_provider_failures_are_reported(self):
        def handler(_request):
            return httpx.Response(503, json={"error": "unavailable"})

        with patch.dict(os.environ, {
            "GREENHOUSE_BOARD_TOKENS": "",
            "LEVER_SITE_NAMES": "",
        }), patch.object(httpx, "AsyncClient", self.client_factory(handler)):
            with self.assertRaises(public_job_providers.ProviderError) as error:
                await public_job_providers.fetch_public_listings(
                    ["Data Analyst"], self.cache_path
                )
        self.assertEqual(error.exception.status_code, 502)
        self.assertIn("Himalayas", str(error.exception))

    async def test_identical_public_requests_are_coalesced(self):
        calls = []

        def handler(_request):
            calls.append(1)
            return httpx.Response(200, json={"jobs": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await asyncio.gather(*(
                public_job_providers._get_json(
                    client,
                    asyncio.Semaphore(2),
                    self.cache_path,
                    "Himalayas",
                    "https://himalayas.app/jobs/api/search?q=Data%20Analyst&page=1",
                )
                for _ in range(8)
            ))
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(cached for _, cached in results), 7)

    async def test_lever_board_paginates_until_the_last_page(self):
        requests = []

        def posting(identifier):
            return {
                "id": f"lever-{identifier}",
                "text": "Software Engineer",
                "applyUrl": f"https://jobs.lever.co/testsite/{identifier}/apply",
                "categories": {"location": "Bengaluru, India"},
            }

        def handler(request):
            requests.append(request)
            skip = parse_qs(request.url.query.decode())["skip"][0]
            if skip == "0":
                return httpx.Response(200, json=[posting(i) for i in range(100)])
            return httpx.Response(200, json=[posting(100)])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs, warnings, succeeded, cache_hits, request_count = (
                await public_job_providers._fetch_lever(
                    client, asyncio.Semaphore(2), self.cache_path, ("testsite",)
                )
            )
        self.assertEqual(len(jobs), 101)
        self.assertFalse(warnings)
        self.assertTrue(succeeded)
        self.assertEqual(cache_hits, 0)
        self.assertEqual(request_count, 2)
        self.assertEqual(
            [parse_qs(request.url.query.decode())["skip"][0] for request in requests],
            ["0", "100"],
        )

    async def test_unknown_himalayas_location_is_not_assumed_india_eligible(self):
        job = public_job_providers._himalayas_job({
            "guid": "h-unknown",
            "title": "Developer",
            "applicationLink": "https://himalayas.app/jobs/h-unknown",
            "locationRestrictions": [],
            "timezoneRestrictions": [],
        })
        self.assertIsNotNone(job)
        self.assertIsNone(job["location_eligible"])
        self.assertIn("eligibility not specified", job["location"])

    async def test_work_mode_is_not_inferred_from_incidental_description_mentions(self):
        job = public_job_providers._greenhouse_job({
            "id": 8,
            "title": "Data Analyst",
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/8",
            "location": {"name": "Bengaluru, India"},
            "content": "<p>Use remote collaboration tools with the analytics team.</p>",
        }, "testco")
        self.assertIsNotNone(job)
        self.assertEqual(job["work_mode"], "")
        explicit_remote = public_job_providers._greenhouse_job({
            "id": 9,
            "title": "Data Analyst",
            "absolute_url": "https://boards.greenhouse.io/testco/jobs/9",
            "location": {"name": "India"},
            "content": "<p>This is a fully remote position open to India.</p>",
        }, "testco")
        self.assertIsNotNone(explicit_remote)
        self.assertEqual(explicit_remote["work_mode"], "remote")
        self.assertTrue(explicit_remote["location_eligible"])

    async def test_unrelated_fuzzy_titles_are_removed_from_search_results(self):
        irrelevant = api_job(
            "people-ops",
            "People Operations Intern",
            "Bengaluru, India",
            "Uses Excel to prepare internal reports.",
            "Greenhouse",
            source_url="https://boards.greenhouse.io/testco",
        )
        with patch(
            "public_job_providers.fetch_public_listings",
            new_callable=AsyncMock,
            return_value={
                "jobs": [irrelevant],
                "provider_warnings": [],
                "providers": ["Greenhouse"],
                "cache_hits": 0,
                "cache_total": 1,
            },
        ):
            result, _ = await job_search.search_listings(
                search_queries=["Data Analyst"],
                target_titles=["Data Analyst"],
                skill_names=["Excel"],
                cache_path=self.cache_path,
            )
        self.assertEqual(result["jobs"], [])


class PublicJobRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="skillbridge-job-route-")
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / "skillbridge.sqlite3"
        self.path_patch = patch.object(main, "DATABASE_PATH", self.database)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.password = "local-test-password"
        main._failed_logins.clear()
        self.client = TestClient(
            main.app,
            base_url=main.FRONTEND_ORIGIN,
            headers={"Origin": main.FRONTEND_ORIGIN},
        )
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def add_employee(self, username, role, skills, location):
        response = self.client.post("/api/auth/register", json={
            "full_name": username.title(),
            "username": username,
            "password": self.password,
        })
        self.assertEqual(response.status_code, 201, response.text)
        profile = {
            "full_name": username.title(),
            "email": "",
            "phone": "",
            "education": "Bachelor of Computer Science",
            "work_experience": "Fresher",
            "skills": [
                {"name": name, "level": None, "source": "resume"}
                for name in skills
            ],
        }
        response = self.client.put("/api/profile", json=profile)
        self.assertEqual(response.status_code, 200, response.text)
        preferences = {
            "preferred_roles": [role] if role else [],
            "location": location,
            "experience_level": "entry",
            "employment_type": "any",
            "minimum_salary": None,
            "career_category": "",
            "work_mode": "any",
        }
        response = self.client.put("/api/job-preferences", json=preferences)
        self.assertEqual(response.status_code, 200, response.text)
        return self.client.get("/api/auth/me").json()["user"]["id"]

    def test_profile_queries_scores_and_preference_changes_are_personalized(self):
        data_id = self.add_employee(
            "data_employee", "Data Analyst", ["Python", "SQL", "Excel"], "Bengaluru"
        )
        data_client = self.client
        with TestClient(
            main.app,
            base_url=main.FRONTEND_ORIGIN,
            headers={"Origin": main.FRONTEND_ORIGIN},
        ) as designer:
            self.client = designer
            design_id = self.add_employee(
                "design_employee", "UI/UX Designer",
                ["Figma", "Wireframing", "Prototyping"], "Mumbai"
            )
            self.client = data_client

            listings = [
                api_job(
                    "analyst-1", "Data Analyst", "Bengaluru, India",
                    "Python, SQL and Excel reporting.", "Greenhouse",
                    source_url="https://boards.greenhouse.io/testco",
                    experience_levels=["entry"],
                ),
                api_job(
                    "designer-1", "UI/UX Designer", "Mumbai, India",
                    "Figma, wireframing and prototyping.", "Lever",
                    source_url="https://jobs.lever.co/testsite",
                    experience_levels=["internship"],
                ),
            ]
            provider_payload = {
                "jobs": listings,
                "provider_warnings": [],
                "providers": ["Greenhouse", "Lever"],
                "cache_hits": 0,
                "cache_total": 1,
            }
            with patch(
                "public_job_providers.fetch_public_listings",
                new_callable=AsyncMock,
                return_value=provider_payload,
            ) as provider:
                data_response = data_client.get("/api/jobs")
                self.client = designer
                design_response = designer.get("/api/jobs")
                self.client = data_client

                self.assertEqual(data_response.status_code, 200, data_response.text)
                self.assertEqual(design_response.status_code, 200, design_response.text)
                data_result, design_result = data_response.json(), design_response.json()
                self.assertEqual(data_result["jobs"][0]["title"], "Data Analyst")
                self.assertEqual(data_result["jobs"][0]["matched_skills"], ["Excel", "Python", "SQL"])
                self.assertEqual(design_result["jobs"][0]["title"], "UI/UX Designer")
                self.assertEqual(
                    design_result["jobs"][0]["matched_skills"],
                    ["Figma", "Prototyping", "Wireframing"],
                )
                self.assertNotEqual(data_result["search_queries"], design_result["search_queries"])
                self.assertEqual(provider.call_count, 2)

                updated_preferences = {
                    "preferred_roles": ["Software Engineer"],
                    "location": "Bengaluru",
                    "experience_level": "entry",
                    "employment_type": "any",
                    "minimum_salary": None,
                    "career_category": "",
                    "work_mode": "any",
                }
                self.assertEqual(
                    data_client.put("/api/job-preferences", json=updated_preferences).status_code,
                    200,
                )
                changed = data_client.get("/api/jobs").json()
                self.assertNotEqual(changed["search_queries"], data_result["search_queries"])
                self.assertIn("Software Engineer", changed["search_queries"])
                manual = data_client.get("/api/jobs?query=Python%20Developer").json()
                self.assertEqual(manual["search_queries"][0], "Python Developer")
        self.assertNotEqual(data_id, design_id)

    def test_empty_search_automatically_uses_confirmed_profile_skills(self):
        self.add_employee(
            "automatic_employee", "", ["Python", "SQL", "Excel"], "Bengaluru"
        )
        with patch(
            "public_job_providers.fetch_public_listings",
            new_callable=AsyncMock,
            return_value={
                "jobs": [],
                "provider_warnings": [],
                "providers": ["Himalayas"],
                "cache_hits": 0,
                "cache_total": 1,
            },
        ) as provider:
            response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["search_queries"][0], "Data Analyst")
        self.assertEqual(provider.await_args.args[0][0], "Data Analyst")

    def test_job_route_reports_provider_errors_and_validates_pagination(self):
        self.add_employee("job_error_employee", "Data Analyst", ["Python", "SQL"], "India")
        with patch(
            "public_job_providers.fetch_public_listings",
            new_callable=AsyncMock,
            side_effect=public_job_providers.ProviderError(502, "Provider is offline."),
        ):
            response = self.client.get("/api/jobs")
        self.assertEqual(response.status_code, 502)
        self.assertIn("Provider is offline", response.json()["detail"])
        self.assertEqual(self.client.get("/api/jobs?page=0").status_code, 422)


class PublicJobFilteringTests(unittest.TestCase):
    def test_role_search_queries_prioritize_exact_and_related_titles(self):
        queries = job_search.generate_search_queries(
            ["Data Analyst"], "entry", limit=3
        )
        self.assertEqual(queries[0], "Data Analyst")
        self.assertIn("junior data analyst", [query.casefold() for query in queries])
        self.assertIn(
            "data analyst fresher",
            [query.casefold() for query in job_search.generate_search_queries(
                ["Data Analyst"], "entry", limit=8
            )],
        )
        self.assertIn(
            "python engineer",
            [query.casefold() for query in job_search.generate_search_queries(
                ["Python Developer"], "entry", limit=3
            )],
        )

    def test_freshers_and_preferences_filter_only_known_conflicts(self):
        junior = api_job(
            "junior", "Python Developer Intern", "Remote · India eligible",
            "Python and SQL internship.", "Himalayas",
            source_url="https://himalayas.app/jobs",
            experience_levels=["internship"],
            location_eligible=True,
            work_mode="remote",
        )
        senior = api_job(
            "senior", "Senior Python Developer", "Bengaluru, India",
            "Requires 8+ years of experience.", "Greenhouse",
            source_url="https://boards.greenhouse.io/testco",
            location_eligible=True,
        )
        unknown = api_job(
            "unknown", "Python Developer", "Location not listed",
            "Python and SQL.", "Lever",
            source_url="https://jobs.lever.co/testsite",
            location_eligible=None,
            work_mode="",
        )
        manager = api_job(
            "manager", "Engineering Manager", "Bengaluru, India",
            "Engineering role with no stated experience requirement.",
            "Lever", source_url="https://jobs.lever.co/testsite",
            location_eligible=True,
        )
        defaults = {
            "experience_level": "entry",
            "location": "India",
            "employment_type": "any",
            "minimum_salary": None,
            "career_category": "",
            "work_mode": "any",
        }
        jobs, removed = job_search.filter_jobs(
            [junior, senior, unknown, manager], **defaults
        )
        self.assertEqual([job["id"] for job in jobs], ["junior"])
        self.assertEqual(removed, 3)
        self.assertEqual(job_search._salary_range("₹40,000 - 50,000 per month"), (480000, 600000))
        self.assertEqual(
            job_search.infer_experience_level({
                "title": "Data Scientist III", "description": "",
            }),
            "mid",
        )

    def test_skill_match_uses_profile_coverage_not_sparse_text_ratio(self):
        job = job_search._normalize_job(api_job(
            "analyst",
            "Data Analyst",
            "Bengaluru, India",
            "Python and SQL reporting.",
            "Himalayas",
            source_url="https://himalayas.app/jobs",
        ))
        profile_skills = {
            "python": {"name": "Python"},
            "sql": {"name": "SQL"},
            "excel": {"name": "Excel"},
            "power bi": {"name": "Power BI"},
        }
        ranked = job_search.score_job(
            job,
            target_titles=["Data Analyst"],
            profile_skills=profile_skills,
            skill_catalog=["Python", "SQL", "Excel", "Power BI"],
            education="",
            work_experience="",
            experience_level="any",
            location="India",
            employment_type="any",
            minimum_salary=None,
            career_category="",
            work_mode="any",
        )
        self.assertEqual(ranked["matched_skills"], ["Python", "SQL"])
        self.assertEqual(ranked["skill_match_percentage"], 50)


if __name__ == "__main__":
    unittest.main()
