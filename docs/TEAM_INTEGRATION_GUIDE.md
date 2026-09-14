# AI-Powered Resume Screener: Team Integration Guide & Handover Notes

**Author**: Sasi (Comprehend NLP & Data Parsing)  
**Date**: September 2026  
**Module**: `nlp-parser` (`nlp_parser.py`, `nlp_lambda.py`, `services/nlp-parser/`)  
**Status**: Completed, verified, and ready for team connection  

---

## 1. Executive Summary for the Team
I have completed the **Comprehend NLP & Data Parsing** module. It extracts standard entities (`PERSON`, `ORGANIZATION`, `DATE`, `TITLE`), normalizes domain skills using custom recognizer integration and canonical alias dictionaries, calculates net professional experience (with overlapping date merging and future date capping), computes audit confidence scores, updates DynamoDB (`processing_status = "PARSED"`), and publishes scoring jobs to SQS.

Below are the exact integration interfaces, payloads, environment variables, and schemas for each team member.

---

## 2. Interface for Virajith (S3 Ingestion & Textract)

### Upstream Handoff
Virajith's ingestion pipeline can trigger my NLP parser via **S3 Event Notification**, **direct Lambda invocation**, or **SQS message**.

#### Option A: S3 Event Trigger (Recommended Serverless Flow)
Once Textract finishes extracting text from a multi-page resume:
1. Save the extracted text or Textract block JSON to:
   ```
   s3://{BUCKET}/jobs/{job_id}/resumes/{candidate_id}/extracted_text.json
   ```
2. S3 `ObjectCreated` event invokes `nlp-parser-lambda`. My Lambda parses the key to automatically obtain `job_id` and `candidate_id` and fetches the text directly.

#### Option B: Direct Lambda Invocation
Virajith's Ingestion Lambda can directly call `nlp-parser-lambda` with payload:
```json
{
  "job_id": "job_2026_001",
  "candidate_id": "cand_0001",
  "text": "Full extracted plain text from Textract...",
  "source_s3_key": "jobs/job_2026_001/resumes/cand_0001/source.pdf",
  "correlation_id": "corr_abc123"
}
```

### Text Preparation Tips for Virajith:
- Preserve line breaks between resume sections (Education, Experience, Skills) so entity boundary detection works optimally.
- If handling multi-page PDFs, concatenate `LINE` blocks in page order.

---

## 3. Interface for Chiranthan (Matching & Queue Processing)

### SQS Scoring Dispatch
As soon as candidate parsing succeeds, my Lambda invokes your `sqs_producer.enqueue_scoring_job` (with direct fallback to `sqs.send_message`) targeting `SCORING_QUEUE_URL`:

```json
{
  "job_id": "job_2026_001",
  "candidate_id": "cand_0001",
  "correlation_id": "corr_abc123"
}
```

### DynamoDB Schema Alignment
I write the exact `parsed_profile` object expected by your `scoring-lambda` (`lambda_function.py` lines 327–389):

| Field in `candidates.parsed_profile` | Python Type | How Chiranthan's Code Consumes It |
|---|---|---|
| `skills` | `List[str]` | Consumed by `compute_skill_score(required_skills, parsed_profile.get("skills", []))` |
| `job_titles` | `List[str]` | Consumed by `compute_title_score(parsed_profile.get("job_titles", []), target_title)` |
| `experience_years` | `float` (stored as `Decimal`) | Consumed by `compute_experience_score(parsed_profile.get("experience_years"), min_exp)` |
| `name` | `str` | Display name for candidate record |
| `employers` | `List[str]` | Organization list for recruiter view |
| `confidence` | `Dict[str, float]` | Per-field extraction confidence metadata |
| `flags` | `List[str]` | Quality assertions (`LOW_CONFIDENCE_NAME`, `MISSING_PHONE`, etc.) |

### Idempotency & Version Guarding
- I set `processing_status = "PARSED"` and initialize `version = 1`.
- This matches Chiranthan's idempotency guard (`profile_version = candidate_item.get("version", 0)`).

---

## 4. Interface for Aryan (Backend APIs, Auth & Notifications)

### API Endpoints Data Contracts

#### 1. Candidate Details: `GET /jobs/{jobId}/candidates/{candidateId}`
Aryan's API handler can read directly from the `candidates` DynamoDB table and return:
```json
{
  "data": {
    "candidate_id": "cand_0001",
    "job_id": "job_2026_001",
    "parsed_profile": {
      "name": "Sarah Jenkins",
      "email": "sarah.jenkins@example.com",
      "phone": "+1 (555) 234-5678",
      "skills": ["AWS", "DynamoDB", "Lambda", "Python", "SQS", "Terraform"],
      "job_titles": ["Cloud Support Associate", "Systems Engineer"],
      "employers": ["Apex Cloud Solutions", "CloudCorp Inc."],
      "experience_years": 3.5,
      "links": {
        "linkedin": "https://linkedin.com/in/sarahjenkins-cloud",
        "github": "https://github.com/sjenkins-dev"
      },
      "confidence": {
        "overall": 0.94,
        "name": 0.99,
        "skills": 0.91,
        "job_titles": 0.92,
        "employers": 0.93
      },
      "flags": []
    },
    "score_total": 86.5,
    "score_breakdown": { "skills": 100.0, "title": 85.0, "experience": 100.0 },
    "processing_status": "SCORED",
    "recruiter_decision": "PENDING"
  }
}
```

#### 2. CSV Shortlist Export: `GET /jobs/{jobId}/shortlist.csv`
The candidate fields extracted by my parser map to your export columns:
- Name: `parsed_profile.name`
- Email: `parsed_profile.email`
- Experience: `parsed_profile.experience_years`
- Skills Matched: `matched_skills` (from Chiranthan)
- Missing Skills: `missing_skills` (from Chiranthan)

#### 3. Privacy & PII Compliance (Spec 9)
All logs in `nlp_lambda.py` automatically mask email and phone using `mask_email()` and `mask_phone()`. Aryan should ensure API logs do the same.

---

## 5. Interface for Shashank (DevOps, DynamoDB, Reliability & QA)

### 1. IAM Execution Role Permissions for `nlp-parser-lambda`
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ComprehendAccess",
      "Effect": "Allow",
      "Action": [
        "comprehend:DetectEntities"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DynamoDBCandidateUpdate",
      "Effect": "Allow",
      "Action": [
        "dynamodb:GetItem",
        "dynamodb:UpdateItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/candidates"
    },
    {
      "Sid": "DynamoDBFailedJobsWrite",
      "Effect": "Allow",
      "Action": [
        "dynamodb:PutItem"
      ],
      "Resource": "arn:aws:dynamodb:*:*:table/failed_jobs"
    },
    {
      "Sid": "SQSSendScoringJob",
      "Effect": "Allow",
      "Action": [
        "sqs:SendMessage"
      ],
      "Resource": "arn:aws:sqs:*:*:resume-scoring-queue"
    },
    {
      "Sid": "S3ResumeRead",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject"
      ],
      "Resource": "arn:aws:s3:::*/*"
    }
  ]
}
```

### 2. Environment Variables Configuration
| Variable Name | Default / Example Value | Description |
|---|---|---|
| `CANDIDATES_TABLE_NAME` | `candidates` | DynamoDB table for applicant records |
| `FAILED_JOBS_TABLE_NAME` | `failed_jobs` | DynamoDB table for permanent failure records |
| `SCORING_QUEUE_URL` | `https://sqs.{region}.amazonaws.com/{acc}/resume-scoring-queue` | SQS queue triggering Chiranthan's scoring lambda |
| `COMPREHEND_CUSTOM_SKILL_ENDPOINT_ARN` | *(Optional ARN)* | Trained custom Comprehend endpoint for SKILL entities |
| `COMPREHEND_LANGUAGE_CODE` | `en` | Language code for Comprehend built-in model |
| `LOG_LEVEL` | `INFO` | Python logging level |

### 3. Failure Handling & Dead-Letter Redrive
- Transient errors (`ThrottlingException`, `ProvisionedThroughputExceededException`) raise `RetryableError` with Lambda SQS partial batch failures (`batchItemFailures`) for automatic retry.
- Corrupt text, missing IDs, or permanent syntax failures write a record directly to `failed_jobs` table:
  - `failure_id`: `fail_xxxxxx`
  - `stage`: `"NLP_PARSING"`
  - `error_code`: `"NLP_PARSE_FAILED"`
  - `safe_error_message`: Non-sensitive safe description

---

## 6. Interface for Prasanna (Frontend Recruiter Dashboard)

### UI Component Binding Reference

| UI Component | Data Source in `candidate` object | Description / UI Behavior |
|---|---|---|
| **Candidate Name** | `candidate.parsed_profile.name` | Prominent header card title |
| **Email & Contact** | `candidate.parsed_profile.email`, `candidate.parsed_profile.phone` | Quick contact button / tooltip |
| **Experience Badge** | `${candidate.parsed_profile.experience_years} yrs` | Displayed on score card badge |
| **Extracted Skills** | `candidate.parsed_profile.skills` | Rendered as clickable pill tags |
| **Candidate Titles** | `candidate.parsed_profile.job_titles` | Subtitle badge beneath candidate name |
| **Employers** | `candidate.parsed_profile.employers` | Prior employer pills |
| **Profile Links** | `candidate.parsed_profile.links.linkedin`, `.github` | Direct clickable social icons |
| **Review Flags** | `candidate.parsed_profile.flags` | Display warning chips if non-empty (e.g. `LOW_CONFIDENCE_NAME`, `MISSING_PHONE`) |
| **Confidence Gauge** | `candidate.parsed_profile.confidence.overall * 100` | Optional recruiter audit gauge (e.g. 94% confidence) |

---

## 7. Demo Datasets (Section 12.1 Compliance)
Three complete synthetic test resumes and their extraction outputs are generated and verified in the repository:
1. **Resume A (`docs/samples/resume_a_conventional.txt` & `.json`)**: Senior Cloud/DevOps Engineer with clear chronological dates, AWS/Python/Lambda/DynamoDB skills, and high extraction confidence.
2. **Resume B (`docs/samples/resume_b_career_switch.txt` & `.json`)**: Mechanical Engineer transitioning to Cloud Engineer, testing transferable skills and non-matching job title handling.
3. **Resume C (`docs/samples/resume_c_sparse_ambiguous.txt` & `.json`)**: Freelance Frontend Developer with sparse formatting, missing phone, and ambiguous date intervals, proving parser resilience and warning flag generation.

All 20 unit tests pass (`python -m unittest discover tests -v`).
