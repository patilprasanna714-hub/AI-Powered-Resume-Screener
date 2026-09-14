# Normalized Parser Schema & NLP Data Contracts

**Module**: Comprehend NLP & Data Parsing  
**Owner**: Sasi  
**Target Lambda**: `nlp-parser-lambda`  
**Status**: Implementation-ready specification  

---

## 1. Overview
The Comprehend NLP & Data Parsing module receives raw resume text (extracted by Virajith via Amazon Textract), applies Amazon Comprehend entity recognition alongside deterministic pattern extraction, and produces a standardized `parsed_profile` item. 

This profile is stored in the DynamoDB `candidates` table and consumed downstream by Chiranthan's `scoring-lambda` to evaluate candidates against job requirements.

---

## 2. Entity Normalization Rules

| Field | Extraction Source | Normalization Rule | Traceability & Fallback |
|---|---|---|---|
| `candidate_name` | Comprehend `PERSON` entity | Highest confidence `PERSON` entity within the header zone (< 500 chars). | Flagged as `LOW_CONFIDENCE_NAME` if confidence < 0.70; allows recruiter override. |
| `email` | Regex `EMAIL_PATTERN` | Normalized to lowercase, stripped of trailing punctuation. | Masked in all CloudWatch logs as `u***r@domain.com` for privacy compliance. |
| `phone` | Regex `PHONE_PATTERN` | Standardized format; validated minimum 7 digits. | Masked in logs as `***-***-1234`. Flagged as `MISSING_PHONE` if omitted. |
| `skills` | Custom Comprehend `SKILL` + Taxonomy | Mapped via canonical `SKILL_ALIASES` (e.g. `"AWS Lambda"` $\rightarrow$ `"Lambda"`, `"Postgres"` $\rightarrow$ `"PostgreSQL"`). Deduplicated and sorted. | Retains average skill confidence. Resilient regex fallback for standard tools. |
| `job_titles` | Comprehend `TITLE` entity | Normalized against `ROLE_TAXONOMY` (e.g. `"Cloud Support Associate"`, `"DevOps Engineer"`). | Flagged as `LOW_CONFIDENCE_TITLE` if score < 0.65. |
| `employers` | Comprehend `ORGANIZATION` entity | Unique organization names; filters out degree names, universities, and tech buzzwords. | Canonical title-case formatting. |
| `experience_years` | Regex intervals + `DATE` entities | Parsed start/end months & years; overlapping intervals are merged; future dates are capped. | Accurately computes net professional years (1 decimal). |
| `confidence` | Comprehend entity confidence scores | Retained across `name`, `skills`, `job_titles`, `employers`, and weighted `overall`. | Stored in candidate profile for recruiter auditability (spec 9.1). |
| `flags` | Rule-based quality assertions | Surface edge cases (`MISSING_EMAIL`, `LOW_CONFIDENCE_NAME`, `FUTURE_START_DATE_CAPPED`). | Displayed on recruiter dashboard badge list. |

---

## 3. Candidate DynamoDB Record Format

When Sasi's Lambda finishes parsing, the DynamoDB `candidates` table item contains:

```json
{
  "job_id": "job_2026_001",
  "candidate_id": "cand_0003",
  "source_s3_key": "jobs/job_2026_001/resumes/cand_0003/source.pdf",
  "processing_status": "PARSED",
  "version": 1,
  "updated_at": "2026-09-14T05:30:00.000Z",
  "parsed_profile": {
    "name": "Sarah Jenkins",
    "email": "sarah.jenkins@example.com",
    "phone": "+1-555-0199",
    "skills": ["AWS", "DynamoDB", "Lambda", "Python", "SQS", "Textract"],
    "job_titles": ["Cloud Support Associate", "Systems Engineer"],
    "employers": ["Apex Cloud Solutions", "CloudCorp Inc."],
    "experience_years": 3.5,
    "links": {
      "linkedin": "https://linkedin.com/in/sarahjenkins-cloud",
      "github": "https://github.com/sjenkins-dev"
    },
    "date_ranges": [
      {
        "raw_start": "Jan 2023",
        "raw_end": "Present",
        "start": "2023-01",
        "end": "2026-09"
      },
      {
        "raw_start": "Jun 2021",
        "raw_end": "Dec 2022",
        "start": "2021-06",
        "end": "2022-12"
      }
    ],
    "confidence": {
      "overall": 0.94,
      "name": 0.99,
      "skills": 0.91,
      "job_titles": 0.92,
      "employers": 0.93
    },
    "flags": []
  }
}
```

---

## 4. Downstream Compatibility Check
- **Chiranthan (`scoring-lambda` / `scoring_logic.py`)**:
  - `compute_skill_score(required_skills, parsed_profile["skills"])` reads the sorted canonical skills list.
  - `compute_title_score(parsed_profile["job_titles"], target_title)` uses normalized job titles.
  - `compute_experience_score(parsed_profile["experience_years"], min_exp)` consumes the float `experience_years`.
  - Duplicate SQS messages are guarded by `version = 1`.
- **Aryan (`api-lambda`)**:
  - GET `/jobs/{jobId}/candidates/{candidateId}` reads `parsed_profile`, `confidence`, and `flags`.
- **Prasanna (`frontend`)**:
  - Recruiter dashboard renders `parsed_profile.name`, `parsed_profile.skills` as tag badges, and `parsed_profile.flags` as warning chips if present.
