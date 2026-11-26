"""Run from backend: python -m unittest test_public_job_search_regression."""
import unittest

import job_search
import public_job_providers


class PublicJobSearchRegression(unittest.TestCase):
    def test_unrestricted_remote_job(self):
        job = public_job_providers._himalayas_job({
            "title": "Software Engineer",
            "applicationLink": "https://example.org/jobs/1",
            "guid": "1",
            "locationRestrictions": [],
            "timezoneRestrictions": [],
        })
        self.assertIs(job["location_eligible"], True)

    def test_india_country_code(self):
        job = public_job_providers._himalayas_job({
            "title": "Software Engineer",
            "applicationLink": "https://example.org/jobs/1",
            "guid": "1",
            "locationRestrictions": ["IN"],
        })
        self.assertIs(job["location_eligible"], True)

    def test_indian_city_matches_country(self):
        self.assertTrue(job_search._location_matches("India", "Bengaluru"))

    def test_explicit_foreign_location(self):
        jobs, removed = job_search.filter_jobs(
            [{"title": "Developer", "location": "London", "location_eligible": False}],
            experience_level="any", location="London", employment_type="any",
            minimum_salary=None, career_category="", work_mode="any",
        )
        self.assertEqual(len(jobs), 1)
        self.assertEqual(removed, 0)


if __name__ == "__main__":
    unittest.main()
