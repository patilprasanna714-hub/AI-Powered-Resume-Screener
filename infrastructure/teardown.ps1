<#
.SYNOPSIS
    Teardown & Cleanup Script for AI-Powered Resume Screener (Windows PowerShell)

.DESCRIPTION
    Empties S3 buckets (resume storage and frontend assets) and deletes the CloudFormation stack.

.PARAMETER Environment
    Target environment stage to tear down: dev (default), staging, prod.

.PARAMETER Region
    AWS region: ap-south-1 (default).
#>

[CmdletBinding()]
param(
    [string]$Environment = "dev",
    [string]$Region = "ap-south-1"
)

$ErrorActionPreference = "Stop"
$StackName = "ai-resume-screener-$Environment"

Write-Host "================================================================" -ForegroundColor Red
Write-Host " AI-Powered Resume Screener - Resource Teardown ($Environment)" -ForegroundColor Red
Write-Host "================================================================" -ForegroundColor Red
Write-Host "Stack Name : $StackName" -ForegroundColor DarkGray
Write-Host "AWS Region : $Region" -ForegroundColor DarkGray
Write-Host ""

$confirm = Read-Host "Are you sure you want to completely tear down '$StackName'? Type 'yes' to proceed"
if ($confirm -ne "yes") {
    Write-Host "Teardown aborted." -ForegroundColor Yellow
    exit 0
}

# Fetch stack outputs to locate S3 buckets
Write-Host "`n[1/3] Identifying S3 buckets to clean..." -ForegroundColor Yellow
try {
    $outputsJson = aws cloudformation describe-stacks `
        --stack-name $StackName `
        --region $Region `
        --query "Stacks[0].Outputs" `
        --output json 2>$null | ConvertFrom-Json

    $outputs = @{}
    foreach ($o in $outputsJson) {
        $outputs[$o.OutputKey] = $o.OutputValue
    }

    $resumeBucket = $outputs["ResumeBucketName"]
    $frontendBucket = $outputs["FrontendBucketName"]

    if ($resumeBucket) {
        Write-Host "  Emptying resume bucket: s3://$resumeBucket..." -ForegroundColor Yellow
        aws s3 rm "s3://$resumeBucket" --recursive --region $Region
        Write-Host "  Emptied resume bucket." -ForegroundColor Green
    }
    if ($frontendBucket) {
        Write-Host "  Emptying frontend bucket: s3://$frontendBucket..." -ForegroundColor Yellow
        aws s3 rm "s3://$frontendBucket" --recursive --region $Region
        Write-Host "  Emptied frontend bucket." -ForegroundColor Green
    }
} catch {
    Write-Host "  Stack not found or outputs unreadable. Proceeding to stack deletion..." -ForegroundColor DarkGray
}

Write-Host "`n[2/3] Deleting CloudFormation stack ($StackName)..." -ForegroundColor Yellow
aws cloudformation delete-stack --stack-name $StackName --region $Region

Write-Host "`n[3/3] Waiting for stack deletion to complete..." -ForegroundColor Yellow
aws cloudformation wait stack-delete-complete --stack-name $StackName --region $Region

Write-Host "`n================================================================" -ForegroundColor Green
Write-Host " TEARDOWN COMPLETE! Stack '$StackName' and resources removed." -ForegroundColor Green
Write-Host "================================================================" -ForegroundColor Green
