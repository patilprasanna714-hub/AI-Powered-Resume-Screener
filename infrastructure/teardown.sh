#!/usr/bin/env bash
# =============================================================================
# Teardown & Cleanup Script for AI-Powered Resume Screener (Bash / Linux)
# =============================================================================
set -euo pipefail

ENVIRONMENT="${1:-dev}"
REGION="${2:-ap-south-1}"
STACK_NAME="ai-resume-screener-${ENVIRONMENT}"

echo "================================================================"
echo " AI-Powered Resume Screener - Resource Teardown (${ENVIRONMENT})"
echo "================================================================"
echo "Stack Name : ${STACK_NAME}"
echo "AWS Region : ${REGION}"
echo ""

read -p "Are you sure you want to completely delete stack '${STACK_NAME}'? (yes/no): " CONFIRM
if [ "${CONFIRM}" != "yes" ]; then
    echo "Teardown cancelled."
    exit 0
fi

echo ""
echo "[1/3] Identifying S3 buckets to clean..."
OUTPUTS=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs" \
    --output json 2>/dev/null || echo "[]")

RESUME_BUCKET=$(echo "${OUTPUTS}" | jq -r '.[] | select(.OutputKey=="ResumeBucketName") | .OutputValue // empty')
FRONTEND_BUCKET=$(echo "${OUTPUTS}" | jq -r '.[] | select(.OutputKey=="FrontendBucketName") | .OutputValue // empty')

if [ -n "${RESUME_BUCKET}" ]; then
    echo "  Emptying resume bucket: s3://${RESUME_BUCKET}..."
    aws s3 rm "s3://${RESUME_BUCKET}" --recursive --region "${REGION}" || true
fi

if [ -n "${FRONTEND_BUCKET}" ]; then
    echo "  Emptying frontend bucket: s3://${FRONTEND_BUCKET}..."
    aws s3 rm "s3://${FRONTEND_BUCKET}" --recursive --region "${REGION}" || true
fi

echo ""
echo "[2/3] Deleting CloudFormation stack (${STACK_NAME})..."
aws cloudformation delete-stack --stack-name "${STACK_NAME}" --region "${REGION}"

echo ""
echo "[3/3] Waiting for stack deletion to complete..."
aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" --region "${REGION}"

echo ""
echo "================================================================"
echo " TEARDOWN COMPLETE! Stack '${STACK_NAME}' removed."
echo "================================================================"
