"""
End-to-End Integration Test: Textract Ingestion -> NLP Parser -> Scoring Pipeline
Simulates the full cross-module workflow between Virajith, Sasi, and Chiranthan.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Provide mock boto3 if not installed in local environment
if "boto3" not in sys.modules:
    mock_boto3 = MagicMock()
    sys.modules["boto3"] = mock_boto3
    sys.modules["botocore"] = MagicMock()
    sys.modules["botocore.exceptions"] = MagicMock()

import ingestion_lambda
import nlp_lambda
from textract_processor import extract_text_from_textract_blocks
from scoring_logic import (
    compute_skill_score,
    compute_title_score,
    compute_experience_score,
    compute_total_score,
    build_score_explanation,
)


class TestEndToEndPipeline(unittest.TestCase):

    def setUp(self):
        # Sample Job Description for Cloud Engineer
        self.sample_jd = {
            "job_id": "job_2026_001",
            "title": "Solutions Architect",
            "required_skills": ["AWS", "Python", "Lambda", "DynamoDB", "Terraform"],
            "minimum_experience": 3.0,
            "threshold": 75.0,
        }

    def test_end_to_end_option_a_s3_event_handoff(self):
        """
        Tests Option A (S3 Event Trigger):
        1. Textract converts resume to structured text preserving section breaks
        2. Ingestion Lambda saves extracted_text.json to S3
        3. S3 Event triggers nlp_lambda.lambda_handler
        4. NLP Lambda normalizes entities and writes PARSED candidate to DynamoDB
        5. Candidate scores against JD requirements using scoring_logic
        """
        # Step 1: Simulate Textract OCR blocks for candidate
        textract_blocks = [
            {"BlockType": "LINE", "Text": "Sarah Jenkins", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.05, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "sarah.jenkins@example.com | +1 (555) 234-5678", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.08, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "https://linkedin.com/in/sarahjenkins-cloud", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.11, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "PROFESSIONAL SUMMARY", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.16, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "Cloud Engineer with 4 years building scalable serverless systems.", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.20, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "TECHNICAL SKILLS", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.26, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "AWS, Python, DynamoDB, Lambda, Terraform, SQS", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.30, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "EXPERIENCE", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.36, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "Cloud Solutions Architect - Apex Cloud Solutions (01/2022 - Present)", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.40, "Left": 0.1, "Height": 0.02}}},
            {"BlockType": "LINE", "Text": "Systems Engineer - CloudCorp Inc. (06/2020 - 12/2021)", "Page": 1, "Geometry": {"BoundingBox": {"Top": 0.44, "Left": 0.1, "Height": 0.02}}},
        ]

        extracted_text = extract_text_from_textract_blocks(textract_blocks)
        self.assertIn("\n\nTECHNICAL SKILLS", extracted_text)
        self.assertIn("\n\nEXPERIENCE", extracted_text)

        # Step 2: Ingestion saves extracted_text.json to S3
        s3_bucket = "resume-screener-demo"
        s3_key = "jobs/job_2026_001/resumes/cand_0001/extracted_text.json"
        source_key = "jobs/job_2026_001/resumes/cand_0001/source.pdf"

        # Step 3: Trigger NLP Lambda via S3 Event
        s3_event = {
            "Records": [
                {
                    "s3": {
                        "bucket": {"name": s3_bucket},
                        "object": {"key": s3_key},
                    }
                }
            ]
        }

        with patch.object(nlp_lambda, "s3") as mock_nlp_s3, \
             patch.object(nlp_lambda, "comprehend") as mock_comprehend, \
             patch.object(nlp_lambda, "candidates_table") as mock_cand_table, \
             patch.object(nlp_lambda, "sqs") as mock_sqs:

            # Mock S3 returning the extracted text payload
            mock_s3_body = MagicMock()
            mock_s3_body.read.return_value = json.dumps({
                "job_id": "job_2026_001",
                "candidate_id": "cand_0001",
                "text": extracted_text,
                "source_s3_key": source_key,
                "Blocks": textract_blocks,
            }).encode("utf-8")
            mock_nlp_s3.get_object.return_value = {"Body": mock_s3_body}

            # Mock Comprehend detecting entities
            mock_comprehend.detect_entities.return_value = {
                "Entities": [
                    {"Type": "PERSON", "Text": "Sarah Jenkins", "Score": 0.99, "BeginOffset": 0, "EndOffset": 13},
                    {"Type": "ORGANIZATION", "Text": "Apex Cloud Solutions", "Score": 0.95, "BeginOffset": 200, "EndOffset": 220},
                    {"Type": "ORGANIZATION", "Text": "CloudCorp Inc.", "Score": 0.92, "BeginOffset": 240, "EndOffset": 254},
                    {"Type": "TITLE", "Text": "Solutions Architect", "Score": 0.94, "BeginOffset": 180, "EndOffset": 199},
                    {"Type": "TITLE", "Text": "Systems Engineer", "Score": 0.90, "BeginOffset": 230, "EndOffset": 246},
                    {"Type": "DATE", "Text": "01/2022 - Present", "Score": 0.92, "BeginOffset": 210, "EndOffset": 227},
                    {"Type": "DATE", "Text": "06/2020 - 12/2021", "Score": 0.91, "BeginOffset": 255, "EndOffset": 272},
                ]
            }

            resp = nlp_lambda.lambda_handler(s3_event, None)
            self.assertEqual(resp["statusCode"], 200)

            # Step 4: Verify DynamoDB received normalized candidate profile
            self.assertTrue(mock_cand_table.update_item.called)
            update_call = mock_cand_table.update_item.call_args[1]
            profile = update_call["ExpressionAttributeValues"][":profile"]

            self.assertEqual(profile["name"], "Sarah Jenkins")
            self.assertEqual(profile["email"], "sarah.jenkins@example.com")
            self.assertIn("AWS", profile["skills"])
            self.assertIn("Python", profile["skills"])
            self.assertIn("Lambda", profile["skills"])
            self.assertIn("DynamoDB", profile["skills"])
            self.assertIn("Terraform", profile["skills"])
            self.assertGreater(float(profile["experience_years"]), 2.0)

            # Step 5: Scoring with Chiranthan's module
            skill_score, matched_skills, missing_skills = compute_skill_score(
                required_skills=self.sample_jd["required_skills"],
                candidate_skills=profile["skills"],
            )
            title_score = compute_title_score(
                candidate_titles=profile["job_titles"],
                target_title=self.sample_jd["title"],
            )
            exp_score = compute_experience_score(
                candidate_years=float(profile["experience_years"]),
                required_years=self.sample_jd["minimum_experience"],
            )
            breakdown = compute_total_score(
                skill_score=skill_score,
                title_score=title_score,
                experience_score=exp_score,
            )
            explanation = build_score_explanation(
                matched_skills=matched_skills,
                missing_skills=missing_skills,
                breakdown=breakdown,
                threshold=self.sample_jd["threshold"],
            )

            self.assertEqual(skill_score, 100.0)
            self.assertEqual(title_score, 100.0)
            self.assertEqual(exp_score, 100.0)
            self.assertEqual(breakdown["total"], 100.0)
            self.assertEqual(explanation["system_recommendation"], "SHORTLIST")

    def test_end_to_end_option_b_direct_invocation_handoff(self):
        """
        Tests Option B (Direct Lambda Invocation payload).
        """
        sample_path = os.path.join(os.path.dirname(__file__), "..", "docs", "samples", "resume_a_conventional.txt")
        with open(sample_path, "r", encoding="utf-8") as f:
            raw_resume_text = f.read()

        payload = {
            "job_id": "job_2026_001",
            "candidate_id": "cand_0001",
            "text": raw_resume_text,
            "source_s3_key": "jobs/job_2026_001/resumes/cand_0001/source.pdf",
            "correlation_id": "corr_test_direct_001",
        }

        with patch.object(nlp_lambda, "comprehend") as mock_comprehend, \
             patch.object(nlp_lambda, "candidates_table") as mock_cand_table, \
             patch.object(nlp_lambda, "sqs") as mock_sqs:

            mock_comprehend.detect_entities.return_value = {
                "Entities": [
                    {"Type": "PERSON", "Text": "Sarah Jenkins", "Score": 0.99, "BeginOffset": 0, "EndOffset": 13},
                    {"Type": "TITLE", "Text": "Senior Cloud Infrastructure Engineer", "Score": 0.95, "BeginOffset": 50, "EndOffset": 85},
                    {"Type": "DATE", "Text": "01/2022 - Present", "Score": 0.9, "BeginOffset": 100, "EndOffset": 117},
                    {"Type": "DATE", "Text": "06/2020 - 12/2021", "Score": 0.9, "BeginOffset": 150, "EndOffset": 167},
                ]
            }

            resp = nlp_lambda.lambda_handler(payload, None)
            self.assertEqual(resp["statusCode"], 200)
            self.assertEqual(resp["body"]["candidate_id"], "cand_0001")
            self.assertEqual(resp["body"]["status"], "PARSED")


if __name__ == "__main__":
    unittest.main()
