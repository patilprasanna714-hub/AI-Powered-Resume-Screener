"""
tests/test_nlp_parser.py
Owner: Sasi — Comprehend NLP & Data Parsing

Comprehensive unit test suite for nlp_parser.py:
- Contact extraction (email, phone, linkedin, github)
- PII masking helpers for secure logging
- Date parsing, overlapping interval merging, future date capping, duration calculation
- Skill normalization and alias mappings
- Title normalization against role taxonomy
- Name extraction heuristic and header-zone prioritization
- End-to-end candidate JSON creation on the 3 required demo resumes (A, B, C)
- Compatibility assertion with Chiranthan's scoring_logic.py
"""

import json
import os
import unittest
from datetime import datetime, timezone

from nlp_parser import (
    calculate_experience_years,
    extract_candidate_name,
    extract_contact_info,
    extract_employers,
    extract_job_titles,
    extract_skills_from_entities_and_text,
    mask_email,
    mask_phone,
    merge_date_intervals,
    normalize_job_title,
    normalize_skill_name,
    parse_and_normalize_resume,
    parse_month_year,
)
from scoring_logic import (
    compute_experience_score,
    compute_skill_score,
    compute_title_score,
    compute_total_score,
)


class TestNLPParser(unittest.TestCase):

    def test_mask_email(self):
        self.assertEqual(mask_email("sarah.jenkins@example.com"), "s***********s@example.com")
        self.assertEqual(mask_email("me@domain.org"), "m*@domain.org")
        self.assertEqual(mask_email(None), "[MASKED_EMAIL]")

    def test_mask_phone(self):
        self.assertEqual(mask_phone("+1 (555) 234-5678"), "***-***-5678")
        self.assertEqual(mask_phone("1234"), "****")
        self.assertEqual(mask_phone(None), "[MASKED_PHONE]")

    def test_extract_contact_info(self):
        sample = (
            "Jane Doe\n"
            "Email: jane.doe@techcorp.io | Tel: +1 555-432-1098\n"
            "LinkedIn: https://linkedin.com/in/janedoe | GitHub: https://github.com/janedoe-dev\n"
        )
        email, phone, links = extract_contact_info(sample)
        self.assertEqual(email, "jane.doe@techcorp.io")
        self.assertIn("555-432-1098", phone)
        self.assertEqual(links.get("linkedin"), "https://linkedin.com/in/janedoe")
        self.assertEqual(links.get("github"), "https://github.com/janedoe-dev")

    def test_parse_month_year(self):
        self.assertEqual(parse_month_year("Jan 2021"), (2021, 1))
        self.assertEqual(parse_month_year("September 2019"), (2019, 9))
        self.assertEqual(parse_month_year("06/2020"), (2020, 6))
        self.assertEqual(parse_month_year("2018"), (2018, 1))
        now = datetime.now(timezone.utc)
        self.assertEqual(parse_month_year("Present"), (now.year, now.month))

    def test_merge_date_intervals(self):
        dt1_start = datetime(2020, 1, 1, tzinfo=timezone.utc)
        dt1_end = datetime(2021, 12, 31, tzinfo=timezone.utc)

        # Overlapping interval (2021-06 to 2022-06)
        dt2_start = datetime(2021, 6, 1, tzinfo=timezone.utc)
        dt2_end = datetime(2022, 6, 30, tzinfo=timezone.utc)

        # Non-overlapping interval (2023-01 to 2023-12)
        dt3_start = datetime(2023, 1, 1, tzinfo=timezone.utc)
        dt3_end = datetime(2023, 12, 31, tzinfo=timezone.utc)

        intervals = [(dt1_start, dt1_end), (dt2_start, dt2_end), (dt3_start, dt3_end)]
        merged = merge_date_intervals(intervals)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0], (dt1_start, dt2_end))
        self.assertEqual(merged[1], (dt3_start, dt3_end))

    def test_calculate_experience_years(self):
        text = (
            "Senior Developer at Corp A\n"
            "January 2020 - December 2021\n"
            "Lead Architect at Corp B\n"
            "January 2022 - December 2023\n"
        )
        years, ranges, flags = calculate_experience_years(text)
        self.assertGreaterEqual(years, 3.8)
        self.assertLessEqual(years, 4.2)
        self.assertEqual(len(ranges), 2)

    def test_skill_normalization_and_aliases(self):
        self.assertEqual(normalize_skill_name("aws lambda"), "Lambda")
        self.assertEqual(normalize_skill_name("amazon dynamodb"), "DynamoDB")
        self.assertEqual(normalize_skill_name("react.js"), "React")
        self.assertEqual(normalize_skill_name("postgres"), "PostgreSQL")
        self.assertEqual(normalize_skill_name("k8s"), "Kubernetes")

    def test_extract_skills_from_entities(self):
        text = "Experience using Python and AWS Lambda with Docker."
        skill_entities = [
            {"Text": "Python", "Type": "SKILL", "Score": 0.99},
            {"Text": "AWS Lambda", "Type": "SKILL", "Score": 0.95},
        ]
        skills, avg_score, _ = extract_skills_from_entities_and_text(text, skill_entities)
        self.assertIn("Python", skills)
        self.assertIn("Lambda", skills)
        self.assertIn("Docker", skills)  # Captured via taxonomy scanner
        self.assertGreater(avg_score, 0.8)

    def test_role_title_normalization(self):
        self.assertEqual(normalize_job_title("cloud support associate"), "Cloud Support Associate")
        self.assertEqual(normalize_job_title("backend developer"), "Backend Engineer")
        self.assertEqual(normalize_job_title("devops engineer"), "DevOps Engineer")

    def test_extract_candidate_name_header_preference(self):
        text = "SARAH JENKINS\nEmail: sarah@example.com\n"
        entities = [
            {"Text": "SARAH JENKINS", "Type": "PERSON", "Score": 0.99, "BeginOffset": 0},
            {"Text": "John Doe", "Type": "PERSON", "Score": 0.95, "BeginOffset": 800},
        ]
        name, score, flags = extract_candidate_name(text, entities)
        self.assertEqual(name, "SARAH JENKINS")
        self.assertEqual(score, 0.99)
        self.assertNotIn("LOW_CONFIDENCE_NAME", flags)

    def test_resume_a_end_to_end(self):
        sample_path = os.path.join("docs", "samples", "resume_a_conventional.txt")
        with open(sample_path, "r", encoding="utf-8") as f:
            text = f.read()

        record = parse_and_normalize_resume(
            raw_text=text,
            job_id="job_2026_001",
            candidate_id="cand_0001",
        )
        profile = record["parsed_profile"]
        self.assertEqual(profile["name"], "SARAH JENKINS")
        self.assertEqual(profile["email"], "sarah.jenkins@example.com")
        self.assertIn("AWS", profile["skills"])
        self.assertIn("Python", profile["skills"])
        self.assertIn("Lambda", profile["skills"])
        self.assertIn("Docker", profile["skills"])
        self.assertGreater(profile["experience_years"], 2.0)
        self.assertEqual(record["processing_status"], "PARSED")
        self.assertEqual(record["version"], 1)

    def test_resume_b_end_to_end_career_switch(self):
        sample_path = os.path.join("docs", "samples", "resume_b_career_switch.txt")
        with open(sample_path, "r", encoding="utf-8") as f:
            text = f.read()

        record = parse_and_normalize_resume(
            raw_text=text,
            job_id="job_2026_001",
            candidate_id="cand_0002",
        )
        profile = record["parsed_profile"]
        self.assertEqual(profile["name"], "MARCUS CHEN")
        self.assertEqual(profile["email"], "marcus.chen@example.com")
        self.assertIn("Python", profile["skills"])
        self.assertIn("Docker", profile["skills"])
        self.assertIn("SQL", profile["skills"])
        self.assertIn("Mechanical Engineer", profile["job_titles"])

    def test_resume_c_end_to_end_sparse_ambiguous(self):
        sample_path = os.path.join("docs", "samples", "resume_c_sparse_ambiguous.txt")
        with open(sample_path, "r", encoding="utf-8") as f:
            text = f.read()

        record = parse_and_normalize_resume(
            raw_text=text,
            job_id="job_2026_001",
            candidate_id="cand_0003",
        )
        profile = record["parsed_profile"]
        self.assertEqual(profile["name"], "AVERY TAYLOR")
        self.assertEqual(profile["email"], "avery.t.dev@example.org")
        self.assertIn("MISSING_PHONE", profile["flags"])
        self.assertIn("React", profile["skills"])

    def test_chiranthan_scoring_compatibility(self):
        """
        Verify that Sasi's parsed_profile feeds directly into Chiranthan's
        scoring_logic.py functions without any key error or type mismatch.
        """
        sample_path = os.path.join("docs", "samples", "resume_a_conventional.txt")
        with open(sample_path, "r", encoding="utf-8") as f:
            text = f.read()

        record = parse_and_normalize_resume(text, job_id="job_1", candidate_id="cand_1")
        parsed = record["parsed_profile"]

        # Job requirements from recruiter
        required_skills = ["AWS", "Python", "Lambda", "DynamoDB", "Terraform"]
        target_title = "Cloud Support Associate"
        min_years = 3.0

        skill_score, matched, missing = compute_skill_score(required_skills, parsed["skills"])
        title_score = compute_title_score(parsed["job_titles"], target_title)
        exp_score = compute_experience_score(parsed["experience_years"], min_years)

        total_breakdown = compute_total_score(skill_score, title_score, exp_score)

        self.assertIsInstance(total_breakdown["total"], float)
        self.assertGreaterEqual(total_breakdown["total"], 0.0)
        self.assertLessEqual(total_breakdown["total"], 100.0)
        self.assertEqual(len(matched) + len(missing), len(required_skills))


if __name__ == "__main__":
    unittest.main()
