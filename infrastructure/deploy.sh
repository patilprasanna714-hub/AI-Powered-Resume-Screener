#!/usr/bin/env bash
# =============================================================================
# Automated Deployment Script for AI-Powered Resume Screener (Bash / Linux / CI)
# =============================================================================
set -euo pipefail

ENVIRONMENT="${1:-dev}"
REGION="${2:-ap-south-1}"
STACK_NAME="ai-resume-screener-${ENVIRONMENT}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

echo "================================================================"
echo " AI-Powered Resume Screener - Full Stack Deployment (${ENVIRONMENT})"
echo "================================================================"
echo "Project Root : ${PROJECT_ROOT}"
echo "AWS Region   : ${REGION}"
echo "Stack Name   : ${STACK_NAME}"
echo ""

# 1. Prerequisite Verification
echo "[1/6] Verifying prerequisites..."
for cmd in aws sam python3 npm; do
    if ! command -v "$cmd" &> /dev/null; then
        if [ "$cmd" = "python3" ] && command -v python &> /dev/null; then
            PYTHON_CMD="python"
        else
            echo "ERROR: Required command '$cmd' is not installed." >&2
            exit 1
        fi
    fi
done
PYTHON_CMD="${PYTHON_CMD:-python3}"

CALLER=$(aws sts get-caller-identity --output json)
echo "  Authenticated AWS Account: $(echo "$CALLER" | jq -r '.Account // empty')"

# 2. Run Backend Tests
echo ""
echo "[2/6] Running backend unit tests (39 tests)..."
cd "${PROJECT_ROOT}"
$PYTHON_CMD -m unittest discover tests -v

# 3. Build Frontend Dashboard
echo ""
echo "[3/6] Building frontend recruiter dashboard..."
cd "${PROJECT_ROOT}/frontend"
if [ ! -d "node_modules" ]; then
    npm install
fi
npm run build

# 4. SAM Build
echo ""
echo "[4/6] Building SAM application..."
cd "${PROJECT_ROOT}"
sam build -t "${SCRIPT_DIR}/template.yaml"

# 5. SAM Deploy
echo ""
echo "[5/6] Deploying CloudFormation stack to AWS (${REGION})..."
sam deploy \
    --template-file "${PROJECT_ROOT}/.aws-sam/build/template.yaml" \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_IAM \
    --resolve-s3 \
    --s3-prefix "${STACK_NAME}" \
    --no-confirm-changeset \
    --parameter-overrides \
        "Environment=${ENVIRONMENT}" \
        "LogLevel=INFO" \
        "DefaultShortlistThreshold=75" \
        "PresignedExpirySeconds=900" \
        "HandoffMode=s3_event"

# 6. Post-Deployment Outputs & Frontend Sync
echo ""
echo "[6/6] Fetching stack outputs & finalizing..."
OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs" \
    --output json)

FRONTEND_BUCKET=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="FrontendBucketName") | .OutputValue')
API_URL=$(echo "$OUTPUTS" | jq -r '.[] | select(.OutputKey=="ApiEndpointUrl") | .OutputValue')

if [ -n "$FRONTEND_BUCKET" ] && [ "$FRONTEND_BUCKET" != "null" ]; then
    echo "  Syncing frontend bundle to s3://${FRONTEND_BUCKET}/..."
    aws s3 sync "${PROJECT_ROOT}/frontend/dist" "s3://${FRONTEND_BUCKET}" --delete --region "${REGION}"
fi

echo ""
echo "================================================================"
echo " DEPLOYMENT SUCCESSFUL!"
echo "================================================================"
echo "  API Endpoint URL  : ${API_URL}"
echo "  Frontend S3 Bucket: ${FRONTEND_BUCKET}"
echo "================================================================"
