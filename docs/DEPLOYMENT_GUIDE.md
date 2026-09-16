# AI-Powered Resume Screener — Cloud Deployment Guide

Comprehensive operations and deployment manual for the serverless recruitment processing pipeline on Amazon Web Services (AWS).

---

## 1. Architectural Overview

The AI-Powered Resume Screener runs as an event-driven serverless workload spanning managed AWS AI services, compute, queues, and storage:

```
[ Recruiter Dashboard (React / Vite) ]
                │
                ▼ (Pre-signed Upload / REST)
       [ Amazon API Gateway ]
                │
                ▼
       [ Amazon S3 (Resume Bucket) ]
                │
                ├──► [ AWS Lambda: Ingestion Function ]
                │         │ (Amazon Textract OCR / DOCX XML)
                │         ▼
                │    [ S3: extracted_text.json ]
                │         │
                │         ▼
                └──► [ AWS Lambda: NLP Parser Function ]
                          │ (Amazon Comprehend NER + Normalization)
                          ▼
                     [ Amazon DynamoDB (candidates) ]
                          │
                          ▼ (Enqueue Job)
                     [ Amazon SQS (resume-scoring-queue) ] ──(Failures x3)──► [ SQS DLQ ]
                          │
                          ▼
                     [ AWS Lambda: Scoring Function ]
                          │ (Weighted Formula & Explanations)
                          ▼
                     [ Amazon DynamoDB (scores & shortlist) ]
```

---

## 2. Infrastructure Inventory (AWS SAM / CloudFormation)

The infrastructure is codified in [`infrastructure/template.yaml`](file:///c:/Users/sasik/Desktop/F13%20Internship/project%204/infrastructure/template.yaml):

| Resource | Logical ID | Type | Description |
|---|---|---|---|
| **Resume Storage** | `ResumeBucket` | `AWS::S3::Bucket` | Encrypted (AES256), private, CORS-configured bucket for resume uploads |
| **Frontend Storage** | `FrontendBucket` | `AWS::S3::Bucket` | S3 bucket for hosting compiled React recruiter dashboard assets |
| **Candidates DB** | `CandidatesTable` | `AWS::DynamoDB::Table` | Table with PK `job_id`, SK `candidate_id`, GSI1 `CandidateLookupIndex`, GSI2 `JobScoreIndex` |
| **Jobs DB** | `JobsTable` | `AWS::DynamoDB::Table` | Table with PK `job_id` and GSI `StatusCreatedAtIndex` for job search/filtering |
| **Failed Jobs DB** | `FailedJobsTable` | `AWS::DynamoDB::Table` | Table with PK `failure_id`, GSI1 `JobFailureIndex`, GSI2 `StatusCreatedIndex` |
| **Audit Events DB** | `AuditEventsTable` | `AWS::DynamoDB::Table` | Immutable log of recruiter decisions and pipeline redrive actions |
| **Scoring Queue** | `ResumeScoringQueue` | `AWS::SQS::Queue` | SQS Queue buffering candidate records for scoring (visibility timeout: 180s) |
| **Scoring DLQ** | `ResumeScoringDLQ` | `AWS::SQS::Queue` | Dead-letter queue storing messages that fail 3 retry attempts |
| **API Gateway** | `ResumeScreenerApi` | `AWS::Serverless::HttpApi` | HTTP API with CORS support for pre-signed upload URLs and direct ingestion |
| **Ingestion Lambda** | `IngestionFunction` | `AWS::Serverless::Function` | Validates file, runs Textract OCR / DOCX parser, and stores extracted text |
| **NLP Parser Lambda**| `NlpParserFunction` | `AWS::Serverless::Function` | Invokes Comprehend, extracts skills/dates/roles, masks PII, updates DynamoDB |
| **Scoring Lambda** | `ScoringFunction` | `AWS::Serverless::Function` | Consumes SQS scoring jobs, calculates weighted match scores, explains ranking |
| **Reliability Lambda**| `ReliabilityFunction`| `AWS::Serverless::Function` | Manages failed_jobs, DLQ poison messages, and self-healing redrive orchestration |
| **Operational Dashboard**| `OperationalDashboard`| `AWS::CloudWatch::Dashboard` | CloudWatch dashboard monitoring queue depths, DLQ messages, and Lambda health |
| **CloudWatch Alarms**| `*Alarm*` | `AWS::CloudWatch::Alarm` | Alarms on DLQ messages ($\ge 1$), queue age ($> 300$s), and Lambda errors |

---

## 3. Environment Variables Reference (Appendix B Checklist)

Each Lambda function uses environment variables to isolate environment dependencies:

### Ingestion Lambda (`resume-screener-ingestion-{env}`)
| Variable | Default Value | Description |
|---|---|---|
| `CANDIDATES_TABLE_NAME` | `candidates-{env}` | Target DynamoDB table for candidate status updates |
| `FAILED_JOBS_TABLE_NAME` | `failed_jobs-{env}` | Dead-letter table for unrecoverable ingestion errors |
| `NLP_LAMBDA_NAME` | `resume-screener-nlp-{env}` | Target Lambda function name for direct handoff |
| `HANDOFF_MODE` | `s3_event` | `s3_event` (recommended) or `direct_invocation` |
| `PRESIGNED_EXPIRY_SECONDS` | `900` | S3 pre-signed upload URL validity lifetime (15 mins) |
| `LOG_LEVEL` | `INFO` | Python logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

### NLP Parser Lambda (`resume-screener-nlp-{env}`)
| Variable | Default Value | Description |
|---|---|---|
| `CANDIDATES_TABLE_NAME` | `candidates-{env}` | DynamoDB table storing normalized candidate profiles |
| `FAILED_JOBS_TABLE_NAME` | `failed_jobs-{env}` | Table capturing NLP parsing failures |
| `SCORING_QUEUE_URL` | *(Resolved via SAM)* | Amazon SQS queue URL for downstream scoring trigger |
| `COMPREHEND_LANGUAGE_CODE` | `en` | Language code for built-in entity detection |
| `COMPREHEND_CUSTOM_SKILL_ENDPOINT_ARN` | *(Optional)* | Custom Comprehend ARN for domain-specific skills |

### Scoring Lambda (`resume-screener-scoring-{env}`)
| Variable | Default Value | Description |
|---|---|---|
| `JOBS_TABLE_NAME` | `jobs-{env}` | DynamoDB table storing target job description criteria |
| `CANDIDATES_TABLE_NAME` | `candidates-{env}` | DynamoDB table storing candidate parsed profiles and scores |
| `FAILED_JOBS_TABLE_NAME` | `failed_jobs-{env}` | Table capturing scoring failures |
| `DEFAULT_SHORTLIST_THRESHOLD` | `75` | Default cutoff percentage for candidate shortlisting |

### Reliability & Redrive Lambda (`resume-screener-reliability-{env}`)
| Variable | Default Value | Description |
|---|---|---|
| `FAILED_JOBS_TABLE_NAME` | `failed_jobs-{env}` | DynamoDB table storing failure and DLQ records |
| `CANDIDATES_TABLE_NAME` | `candidates-{env}` | DynamoDB table storing candidate profiles |
| `AUDIT_EVENTS_TABLE_NAME`| `audit_events-{env}` | DynamoDB table storing immutable audit logs |
| `SCORING_QUEUE_URL` | *(Resolved via SAM)* | SQS queue for re-enqueueing redriven scoring jobs |
| `NLP_LAMBDA_NAME` | `resume-screener-nlp-{env}` | Target NLP Lambda for re-dispatching parsing jobs |
| `INGESTION_LAMBDA_NAME` | `resume-screener-ingestion-{env}` | Target Ingestion Lambda for re-dispatching file jobs |

---

## 4. Local Deployment Instructions

### Prerequisites
1. **AWS CLI** (v2+) authenticated (`aws configure` or `aws sts get-caller-identity`).
2. **AWS SAM CLI** (v1.100+) installed.
3. **Python 3.11+** installed.
4. **Node.js 20+** and **npm** installed.

### Option A: Windows PowerShell (One-Click)
Run the automated deployment script from the project root:
```powershell
.\infrastructure\deploy.ps1 -Environment dev -Region ap-south-1
```

Available flags:
- `-Environment dev` (or `staging`, `prod`)
- `-Region ap-south-1` (or your preferred region)
- `-SkipTests` (skips the 39 unit tests)
- `-SkipFrontend` (skips compiling and uploading the React dashboard)

### Option B: Linux / macOS / Bash
```bash
chmod +x infrastructure/deploy.sh
./infrastructure/deploy.sh dev ap-south-1
```

### Option C: Manual AWS SAM Commands
```bash
# 1. Run backend tests
python -m unittest discover tests -v

# 2. Build the serverless application
sam build -t infrastructure/template.yaml

# 3. Deploy to AWS
sam deploy \
  --config-file infrastructure/samconfig.toml \
  --stack-name ai-resume-screener-dev \
  --region ap-south-1 \
  --parameter-overrides \
      "Environment=dev LogLevel=INFO DefaultShortlistThreshold=75"

# 4. Build and sync frontend
cd frontend
npm install
npm run build
aws s3 sync dist/ s3://<FrontendBucketName>/ --delete
```

---

## 5. Automated CI/CD Deployment (GitHub Actions)

Continuous deployment is preconfigured in [`.github/workflows/deploy.yml`](file:///c:/Users/sasik/Desktop/F13%20Internship/project%204/.github/workflows/deploy.yml).

### Setting up Repository Secrets
In your GitHub repository settings, navigate to **Settings > Secrets and variables > Actions** and add:
- `AWS_ACCESS_KEY_ID`: IAM user access key with deployment permissions.
- `AWS_SECRET_ACCESS_KEY`: IAM user secret access key.
- `AWS_REGION`: Target region (e.g., `ap-south-1`).

### Workflow Execution
- Every push to the `main` branch automatically triggers the validation suite (39 tests + frontend lint) and updates the AWS stack.
- Manual deployments can be triggered via GitHub's **Run workflow** button with your choice of `dev`, `staging`, or `prod`.

---

## 6. Post-Deployment Verification & Smoke Testing

### 1. Retrieve Stack Endpoints
```powershell
aws cloudformation describe-stacks `
  --stack-name ai-resume-screener-dev `
  --region ap-south-1 `
  --query "Stacks[0].Outputs" `
  --output table
```

### 2. Smoke Test: Generate Pre-Signed Upload URL
```bash
curl -X POST "<ApiEndpointUrl>/resumes/upload-url" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "job_001", "candidate_id": "cand_smoke_01", "filename": "resume.pdf"}'
```
Expected response:
```json
{
  "upload_url": "https://resume-screener-dev-...s3.amazonaws.com/jobs/job_001/resumes/cand_smoke_01/source.pdf?...",
  "s3_bucket": "resume-screener-dev-...",
  "s3_key": "jobs/job_001/resumes/cand_smoke_01/source.pdf",
  "expires_in": 900
}
```

### 3. Smoke Test: Ingestion & NLP Parsing (Direct API)
```bash
curl -X POST "<ApiEndpointUrl>/resumes/parse" \
  -H "Content-Type: application/json" \
  -d '{"job_id": "job_001", "candidate_id": "cand_smoke_01", "text": "Senior Python and AWS Engineer with 6 years experience in Lambda and DynamoDB. Email: dev@example.com Phone: 555-0199"}'
```

### 4. Smoke Test: Failed Jobs Review & Redrive API
```bash
# 1. Query open failures (supports ?status=FAILED, ?job_id=job_001, ?limit=10)
curl -X GET "<ApiEndpointUrl>/failed-jobs?status=FAILED"

# 2. Trigger automated self-healing redrive
curl -X POST "<ApiEndpointUrl>/failed-jobs/<failure_id>/retry" \
  -H "x-actor-sub: admin_reviewer"
```
Expected redrive response:
```json
{
  "success": true,
  "data": {
    "failure_id": "fail_31adc6bb46ed",
    "status": "RETRIED",
    "retry_count": 1,
    "stage": "SCORING",
    "dispatched": true,
    "timestamp": "2026-09-16T12:02:15Z"
  }
}
```

---

## 7. Teardown & Resource Cleanup

To avoid ongoing AWS charges after evaluation or testing:

### Windows PowerShell
```powershell
.\infrastructure\teardown.ps1 -Environment dev -Region ap-south-1
```

### Linux / macOS
```bash
chmod +x infrastructure/teardown.sh
./infrastructure/teardown.sh dev ap-south-1
```

The script automatically:
1. Identifies and recursively empties the Resume storage and Frontend S3 buckets.
2. Deletes the CloudFormation stack and all provisioned AWS resources.
3. Verifies full deletion with `aws cloudformation wait stack-delete-complete`.
