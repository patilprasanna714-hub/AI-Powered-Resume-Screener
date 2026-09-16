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

## Shashank's Module: DynamoDB, DevOps, Reliability & QA

### Responsibilities:
1. **DynamoDB Data Layer**:
   - Codified all 4 operational tables (`jobs`, `candidates`, `failed_jobs`, `audit_events`) with complete Global Secondary Indexes (GSIs), Point-in-Time Recovery (PITR), and encryption at rest.
2. **Reliability & Dead-Letter Redrive**:
   - Automated Dead-Letter Queue (DLQ) consumer capturing poison messages after retry exhaustion (`maxReceiveCount: 3`).
   - Built self-healing redrive service (`POST /failed-jobs/{failureId}/retry` and `GET /failed-jobs`) with stage-specific re-dispatch (`SCORING`, `NLP_PARSING`, `INGESTION_TEXTRACT`) and immutable audit trails in `audit_events`.
   - Implemented bounded retry limits (`MAX_REDRIVE_ATTEMPTS = 5`) and automatic PII sanitization across logs and database records.
3. **CloudWatch Observability & Alarms**:
   - Provisioned real-time CloudWatch alarms for DLQ messages, queue age SLO breaches (>300s), and Lambda execution errors across all functions.
   - Codified an operational dashboard (`resume-screener-operational-dashboard`) tracking queue depths, Lambda durations, errors, and DynamoDB metrics.
4. **DevOps Automation & QA**:
   - Authored one-click deployment and teardown scripts (`deploy.ps1`, `deploy.sh`, `teardown.ps1`, `teardown.sh`) and GitHub Actions CI/CD workflows.
   - Implemented 29 specialized QA tests covering reliability, security compliance, burst load, and resilience.

---

## Directory Structure
```
├── textract_processor.py           # Virajith: Textract OCR, section spacing & DOCX parser
├── ingestion_lambda.py             # Virajith: AWS Lambda handler (ingestion-lambda)
├── nlp_parser.py                   # Sasi: Pure NLP parser, normalization & date math
├── nlp_lambda.py                   # Sasi: AWS Lambda handler (nlp-parser-lambda)
├── lambda_function.py              # Chiranthan: Scoring Lambda (scoring-lambda)
├── scoring_logic.py                # Chiranthan: Deterministic scoring formulas
├── jd_parser.py                    # Chiranthan: JD requirement extractor
├── sqs_producer.py                 # Chiranthan: SQS enqueue helper
├── reliability_lambda.py           # Shashank: AWS Lambda handler (reliability & redrive)
├── services/
│   ├── ingestion/
│   │   └── lambda_function.py      # Virajith: Modular Ingestion Lambda entry point
│   ├── nlp-parser/
│   │   └── lambda_function.py      # Sasi: Modular NLP Parser Lambda entry point
│   ├── scoring/
│   │   └── lambda_function.py      # Chiranthan: Modular Scoring Lambda entry point
│   └── reliability/
│       ├── __init__.py             # Shashank: Reliability module package
│       ├── failed_jobs_service.py  # Shashank: Failure tracking, DLQ consumer & redrive logic
│       └── lambda_function.py      # Shashank: Modular Reliability Lambda entry point
├── infrastructure/                 # Shashank / Shared: IaC & Deployment Automation
│   ├── template.yaml               # AWS SAM template (S3, DynamoDB, SQS, Lambdas, API Gateway, Alarms, Dashboard)
│   ├── samconfig.toml              # SAM deployment configuration for ap-south-1
│   ├── deploy.ps1                  # One-click Windows PowerShell deployment script
│   ├── deploy.sh                   # Linux/macOS/CI Bash deployment script
│   ├── teardown.ps1                # Clean resource teardown script (Windows)
│   └── teardown.sh                 # Clean resource teardown script (Linux)
├── .github/workflows/
│   ├── ci.yml                      # CI: Automated tests and frontend linting
│   └── deploy.yml                  # CD: Automated deployment to AWS on push
├── docs/
│   ├── DEPLOYMENT_GUIDE.md         # Comprehensive AWS cloud deployment & operations guide
│   ├── RELIABILITY_AND_QA_EVIDENCE.md # Shashank: Reliability, monitoring & QA test evidence report
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
│   ├── test_end_to_end_pipeline.py # 2 integration tests (Ingestion -> NLP -> Scoring)
│   ├── test_reliability.py         # 16 unit & integration tests for failed_jobs, DLQ & redrive
│   ├── test_security_audit.py      # 8 security tests for S3 blocks, encryption, IAM & PII masking
│   └── test_load_and_resilience.py # 5 resilience tests for burst load, partial failures & retry limits
└── frontend/                       # Prasanna: React dashboard (Vite + React 19)
```

---

## Running Tests
Run all 68 unit, integration, reliability, security, and resilience tests with Python's built-in `unittest` runner:
```bash
python -m unittest discover tests -v
```

---

## Deployment to AWS

The project includes an AWS SAM infrastructure stack and automated deployment scripts.

### One-Click Deployment (Windows PowerShell)
```powershell
.\infrastructure\deploy.ps1 -Environment dev -Region ap-south-1
```

### One-Click Deployment (Linux / macOS)
```bash
chmod +x infrastructure/deploy.sh
./infrastructure/deploy.sh dev ap-south-1
```

### Automated GitHub Actions CI/CD
Deployments are continuously delivered via [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml) on push to the `main` branch or manual `workflow_dispatch`.

For full deployment details, environment variables checklist, and post-deployment smoke tests, see the [Cloud Deployment Guide](docs/DEPLOYMENT_GUIDE.md).

---

## Resource Teardown
To safely delete the CloudFormation stack and empty all S3 storage buckets:
```powershell
.\infrastructure\teardown.ps1 -Environment dev -Region ap-south-1
```

