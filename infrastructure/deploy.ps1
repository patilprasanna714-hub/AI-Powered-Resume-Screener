<#
.SYNOPSIS
    Automated Deployment Script for AI-Powered Resume Screener (Windows PowerShell)

.DESCRIPTION
    Builds, tests, and deploys the serverless backend (S3, DynamoDB, SQS, Lambdas, API Gateway)
    and the React recruiter frontend dashboard to AWS.

.PARAMETER Environment
    Target environment stage: dev (default), staging, prod.

.PARAMETER Region
    AWS region to deploy to: ap-south-1 (default).

.PARAMETER SkipTests
    Skip running python unit/integration tests before deployment.

.PARAMETER SkipFrontend
    Skip building and syncing frontend dashboard to S3.
#>

[CmdletBinding()]
param(
    [string]$Environment = "dev",
    [string]$Region = "ap-south-1",
    [switch]$SkipTests,
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"
$StackName = "ai-resume-screener-$Environment"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path "$ScriptDir\.."

Write-Host "================================================================" -ForegroundColor Cyan
Write-Host " AI-Powered Resume Screener - Full Stack Deployment ($Environment)" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Cyan
Write-Host "Project Root : $ProjectRoot" -ForegroundColor DarkGray
Write-Host "AWS Region   : $Region" -ForegroundColor DarkGray
Write-Host "Stack Name   : $StackName" -ForegroundColor DarkGray
Write-Host ""

# ---------------------------------------------------------------------------
# 1. Prerequisite Verification
# ---------------------------------------------------------------------------
Write-Host "[1/6] Verifying prerequisites..." -ForegroundColor Yellow

$requiredCommands = @("aws", "sam", "python")
if (-not $SkipFrontend) {
    $requiredCommands += "npm"
}

foreach ($cmd in $requiredCommands) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        Write-Error "Required CLI tool '$cmd' was not found in PATH. Please install it before deploying."
    }
}

# Verify AWS identity
$caller = aws sts get-caller-identity --output json | ConvertFrom-Json
Write-Host "  Authenticated AWS Account : $($caller.Account)" -ForegroundColor Green
Write-Host "  IAM Principal             : $($caller.Arn)" -ForegroundColor Green

# ---------------------------------------------------------------------------
# 2. Run Backend Unit & Integration Tests
# ---------------------------------------------------------------------------
if (-not $SkipTests) {
    Write-Host "`n[2/6] Running backend unit tests (39 tests)..." -ForegroundColor Yellow
    Push-Location $ProjectRoot
    try {
        python -m unittest discover tests -v
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Backend tests failed! Aborting deployment."
        }
        Write-Host "  All backend tests passed successfully!" -ForegroundColor Green
    }
    finally {
        Pop-Location
    }
} else {
    Write-Host "`n[2/6] Skipping backend tests (-SkipTests specified)." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 3. Build Frontend Dashboard (React + Vite)
# ---------------------------------------------------------------------------
if (-not $SkipFrontend) {
    Write-Host "`n[3/6] Building frontend recruiter dashboard..." -ForegroundColor Yellow
    Push-Location "$ProjectRoot\frontend"
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "  Installing frontend dependencies (npm install)..." -ForegroundColor DarkGray
            npm install
        }
        Write-Host "  Compiling production assets (npm run build)..." -ForegroundColor DarkGray
        npm run build
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Frontend build failed! Aborting deployment."
        }
        Write-Host "  Frontend compiled successfully to frontend/dist!" -ForegroundColor Green
    }
    finally {
        Pop-Location
    }
} else {
    Write-Host "`n[3/6] Skipping frontend build (-SkipFrontend specified)." -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 4. SAM Build
# ---------------------------------------------------------------------------
Write-Host "`n[4/6] Building SAM serverless application..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    sam build -t "$ScriptDir\template.yaml"
    if ($LASTEXITCODE -ne 0) {
        Write-Error "SAM build failed!"
    }
    Write-Host "  SAM application built successfully!" -ForegroundColor Green
}
finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# 5. SAM Deploy
# ---------------------------------------------------------------------------
Write-Host "`n[5/6] Deploying CloudFormation stack to AWS ($Region)..." -ForegroundColor Yellow
Push-Location $ProjectRoot
try {
    sam deploy `
        --template-file "$ProjectRoot\.aws-sam\build\template.yaml" `
        --stack-name $StackName `
        --region $Region `
        --capabilities CAPABILITY_IAM `
        --resolve-s3 `
        --s3-prefix $StackName `
        --no-confirm-changeset `
        --parameter-overrides "Environment=$Environment LogLevel=INFO DefaultShortlistThreshold=75 PresignedExpirySeconds=900 HandoffMode=s3_event"

    if ($LASTEXITCODE -ne 0) {
        Write-Error "SAM deploy failed!"
    }
    Write-Host "  SAM deployment completed successfully!" -ForegroundColor Green
}
finally {
    Pop-Location
}

# ---------------------------------------------------------------------------
# 6. Post-Deployment Outputs & Frontend S3 Sync
# ---------------------------------------------------------------------------
Write-Host "`n[6/6] Fetching stack outputs & finalizing..." -ForegroundColor Yellow

$outputsJson = aws cloudformation describe-stacks `
    --stack-name $StackName `
    --region $Region `
    --query "Stacks[0].Outputs" `
    --output json | ConvertFrom-Json

$outputs = @{}
foreach ($o in $outputsJson) {
    $outputs[$o.OutputKey] = $o.OutputValue
}

$frontendBucket = $outputs["FrontendBucketName"]
if (-not $SkipFrontend -and $frontendBucket) {
    Write-Host "  Syncing frontend bundle to s3://$frontendBucket/..." -ForegroundColor Yellow
    aws s3 sync "$ProjectRoot\frontend\dist" "s3://$frontendBucket" --delete --region $Region
    Write-Host "  Frontend assets synced successfully!" -ForegroundColor Green
}

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host " DEPLOYMENT SUCCESSFUL!" -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
Write-Host "  API Endpoint URL  : $($outputs['ApiEndpointUrl'])" -ForegroundColor Cyan
Write-Host "  Resume S3 Bucket  : $($outputs['ResumeBucketName'])" -ForegroundColor White
Write-Host "  Frontend S3 Bucket: $($outputs['FrontendBucketName'])" -ForegroundColor White
Write-Host "  Candidates Table  : $($outputs['CandidatesTableName'])" -ForegroundColor White
Write-Host "  Jobs Table        : $($outputs['JobsTableName'])" -ForegroundColor White
Write-Host "  Failed Jobs Table : $($outputs['FailedJobsTableName'])" -ForegroundColor White
Write-Host "  Scoring SQS Queue : $($outputs['ScoringQueueUrl'])" -ForegroundColor White
Write-Host "  Scoring DLQ SQS   : $($outputs['ScoringDLQUrl'])" -ForegroundColor White
Write-Host "================================================================" -ForegroundColor Green
