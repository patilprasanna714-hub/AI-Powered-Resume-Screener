# AI-Powered-Resume-Screener
AI-powered recruitment pipeline using AWS Textract, Amazon Comprehend, AWS Lambda, Amazon SQS, Amazon DynamoDB, and React.

---

## Team Allocation & Modules (Technical Spec v1.0)
- **Virajith**: S3 Ingestion & Textract (`textract_processor.py`, `ingestion_lambda.py`, `services/ingestion/`)
- **Sasi**: Comprehend NLP & Data Parsing (`nlp_parser.py`, `nlp_lambda.py`, `services/nlp-parser/`)
- **Chiranthan**: Matching & Queue Processing (`lambda_function.py`, `scoring_logic.py`, `jd_parser.py`, `sqs_producer.py`)
- **Aryan**: Backend APIs, Cognito Auth & SES Notifications (`services/api/`)
- **Shashank**: DynamoDB, DevOps, Reliability & QA (`infrastructure/`)
- **Prasanna**: Frontend Recruiter Dashboard (`frontend/`)

---

## Virajith's Module: S3 Ingestion & Textract

### Responsibilities:
1. **Resume Ingestion & Validation**:
   - Validates uploaded file type (`.pdf`, `.docx`, `.doc`, `.txt`) and enforce size bounds ($\le 10$ MB).
   - Generates pre-signed S3 upload URLs under prefix `jobs/{job_id}/resumes/{candidate_id}/source.ext`.
2. **Document OCR & Parsing**:
   - Extracts machine-readable text from PDFs using Amazon Textract (`detect_document_text` / `start_document_text_detection`).
   - Extracts DOCX files using standard XML package parsing without external dependencies.
   - **Section Line Break Preservation**: Intelligently joins `LINE` blocks and geometry gaps so sections (`EXPERIENCE`, `SKILLS`, `EDUCATION`) retain line breaks and entity boundaries for NLP extraction.
3. **Downstream NLP Handoff**:
   - **Option A (S3 Event Trigger - Recommended)**: Saves formatted extracted text payload to `s3://{BUCKET}/jobs/{job_id}/resumes/{candidate_id}/extracted_text.json`, automatically triggering `nlp-parser-lambda`.
   - **Option B (Direct Lambda Invocation)**: Invokes `nlp-parser-lambda` directly with `{job_id, candidate_id, text, source_s3_key, correlation_id}`.
4. **Reliability & State Tracking**:
   - Tracks candidate state in DynamoDB (`PROCESSING` $\rightarrow$ `EXTRACTED`).
   - Routes permanent file validation and extraction errors to `failed_jobs` DynamoDB table.

---

## Sasi's Module: Comprehend NLP & Data Parsing

### Responsibilities:
1. **Comprehend Extraction**: Extract standard entities (`PERSON`, `ORGANIZATION`, `DATE`, `TITLE`) and custom `SKILL` entities via Amazon Comprehend.
2. **Deterministic Normalization**:
   - Skill canonicalization through domain alias dictionary (`SKILL_ALIASES`).
   - Role title normalization against standard role taxonomy (`ROLE_TAXONOMY`).
   - Net professional experience years calculation with overlapping date interval merging and future date capping.
   - Deterministic regex extraction of email, phone, and profile links (LinkedIn, GitHub).
   - PII masking helpers for privacy-safe CloudWatch logging (`mask_email`, `mask_phone`).
3. **Audit & Traceability**:
   - Retains per-field extraction confidence scores (`overall`, `name`, `skills`, `job_titles`, `employers`).
   - Surfaces data quality audit flags (e.g., `LOW_CONFIDENCE_NAME`, `MISSING_PHONE`).
4. **DynamoDB & SQS Integration**:
   - Persists normalized candidate item to DynamoDB `candidates` table with status `PARSED` and optimistic locking `version=1`.
   - Dispatches candidate to SQS `resume-scoring-queue` for Chiranthan's scoring lambda.
   - Routes permanent errors to `failed_jobs` table and transient errors to Lambda retry.

---

## Directory Structure
```
├── textract_processor.py           # Virajith: Textract OCR, section spacing & DOCX parser
├── ingestion_lambda.py             # Virajith: AWS Lambda handler (ingestion-lambda)
├── services/
│   ├── ingestion/
│   │   └── lambda_function.py      # Virajith: Modular Ingestion Lambda entry point
│   └── nlp-parser/
│       └── lambda_function.py      # Sasi: Modular NLP Parser Lambda entry point
├── nlp_parser.py                   # Sasi: Pure NLP parser, normalization & date math
├── nlp_lambda.py                   # Sasi: AWS Lambda handler (nlp-parser-lambda)
├── lambda_function.py              # Chiranthan: Scoring Lambda (scoring-lambda)
├── scoring_logic.py                # Chiranthan: Deterministic scoring formulas
├── jd_parser.py                    # Chiranthan: JD requirement extractor
├── sqs_producer.py                 # Chiranthan: SQS enqueue helper
├── docs/
│   ├── normalized_parser_schema.json  # Sasi: JSON Schema draft-07 specification
│   ├── nlp_schema.md                  # Sasi: Detailed data dictionary & contracts
│   ├── TEAM_INTEGRATION_GUIDE.md      # Sasi: Handoff specs for all 5 teammates
│   └── samples/                       # Sasi: Section 12.1 Demo Dataset
│       ├── resume_a_conventional.txt
│       ├── resume_a_extraction.json
│       ├── resume_b_career_switch.txt
│       ├── resume_b_extraction.json
│       ├── resume_c_sparse_ambiguous.txt
│       └── resume_c_extraction.json
├── tests/
│   ├── test_textract_processor.py  # 10 unit tests for Textract block formatting & DOCX parsing
│   ├── test_ingestion_lambda.py    # 7 unit tests for Ingestion Lambda, S3 events & pre-signed URLs
│   ├── test_nlp_parser.py          # 14 unit tests for NLP parsing & scoring compatibility
│   ├── test_nlp_lambda.py          # 6 unit tests for Lambda handling, DynamoDB & SQS
│   └── test_end_to_end_pipeline.py # End-to-end integration tests (Ingestion -> NLP -> Scoring)
└── frontend/                       # Prasanna: React dashboard
```

---

## Running Tests
Run all unit and integration tests with Python's built-in `unittest` runner (zero external dependencies needed):
```bash
python -m unittest discover tests -v
```
