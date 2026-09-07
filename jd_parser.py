"""
jd_parser.py
Owner: Chiranthan — JD parsing portion of Matching & Queue Processing.

Runs when a recruiter creates/updates a job (called from the job-creation
Lambda, or invoked here on a job-creation S3/API event). Extracts the job's
required skills and target title from free-text JD content using the same
Comprehend building blocks Sasi's resume parser uses, so both sides of the
match use identically-normalized entities.

This module does NOT call Textract — JDs are assumed to already be plain
text (typed into the form) or pre-extracted upstream if uploaded as a file.
"""

import os
import importlib

# Load the optional AWS SDK without requiring the editor's Python environment
# to resolve boto3 as a direct import.
boto3 = importlib.import_module("boto3")

COMPREHEND_LANGUAGE_CODE = "en"
SKILL_ENTITY_TYPE = "SKILL"  # as emitted by the custom entity recognizer endpoint

comprehend = boto3.client("comprehend")

CUSTOM_SKILL_ENDPOINT_ARN = os.environ.get("COMPREHEND_CUSTOM_SKILL_ENDPOINT_ARN")


def extract_jd_requirements(jd_text: str) -> dict:
    """
    Returns:
        {
          "required_skills": [...],
          "candidate_titles_seen": [...],   # TITLE entities found in the JD, for reference
          "target_title": "..."             # first/most confident TITLE entity
        }
    """
    if not jd_text or not jd_text.strip():
        return {"required_skills": [], "candidate_titles_seen": [], "target_title": None}

    # Standard entities: pulls TITLE (and others) directly from Comprehend's built-in model.
    standard_resp = comprehend.detect_entities(
        Text=jd_text[:5000],  # Comprehend sync API limit is 5000 UTF-8 bytes per call
        LanguageCode=COMPREHEND_LANGUAGE_CODE,
    )
    titles = [e["Text"] for e in standard_resp.get("Entities", []) if e["Type"] == "TITLE"]

    required_skills = []
    if CUSTOM_SKILL_ENDPOINT_ARN:
        # Custom recognizer endpoint trained on skill entities (spec 6.2 / 15).
        custom_resp = comprehend.detect_entities(
            Text=jd_text[:5000],
            EndpointArn=CUSTOM_SKILL_ENDPOINT_ARN,
        )
        required_skills = sorted({
            e["Text"].strip().lower()
            for e in custom_resp.get("Entities", [])
            if e.get("Type") == SKILL_ENTITY_TYPE and e.get("Score", 0) >= 0.5
        })

    target_title = titles[0] if titles else None

    return {
        "required_skills": required_skills,
        "candidate_titles_seen": titles,
        "target_title": target_title,
    }
