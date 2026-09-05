$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $scriptDir "..\hybrid_agentic_pension_qwen\models\salary_growth\catboost_m3.cbm"
$targetDir = Join-Path $scriptDir "models"
$target = Join-Path $targetDir "catboost_m3.cbm"

if (-not (Test-Path $source)) {
    throw "Source model not found: $source"
}

New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
Copy-Item -Force $source $target

Write-Host "[OK] Model copied to: $target"
