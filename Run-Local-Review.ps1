param(
    [ValidateRange(1024, 65535)][int]$Port = 8770,
    [string]$PythonExecutable = '',
    [switch]$SkipInstall
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$reviewRoot = $PSScriptRoot
$reviewVenv = Join-Path $reviewRoot '.venv-local-review'
$reviewPython = Join-Path $reviewVenv 'Scripts\python.exe'
$reviewData = Join-Path $reviewRoot '.local-review-data'

function Assert-Success([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)." }
}

if (-not (Test-Path -LiteralPath $reviewPython)) {
    if ($PythonExecutable) {
        & $PythonExecutable -c 'import sys; assert sys.version_info[:2] == (3, 11), ''Python 3.11 is required'''
        Assert-Success 'Python version check'
        & $PythonExecutable -m venv $reviewVenv
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3.11 -m venv $reviewVenv
    } else {
        & python -c 'import sys; assert sys.version_info[:2] == (3, 11), ''Install Python 3.11 or specify -PythonExecutable'''
        Assert-Success 'Python version check'
        & python -m venv $reviewVenv
    }
    Assert-Success 'Virtual environment creation'
}
& $reviewPython -c 'import platform, sys; assert sys.version_info[:2] == (3, 11) and sys.implementation.name == ''cpython'' and sys.platform == ''win32'' and platform.machine() == ''AMD64'' and sys.maxsize > 2**32, ''The review environment must use Windows x64 CPython 3.11'''
Assert-Success 'Review environment version check'

Push-Location -LiteralPath $reviewRoot
$priorEnvironment = @{}
try {
    $requirements = Join-Path $reviewRoot 'requirements-local-win64-py311.lock'
    # Python is already verified above. Avoid depending on PowerShell module
    # discovery, which differs when Windows PowerShell is launched from pwsh.
    $requirementsDigest = & $reviewPython -c 'import hashlib, sys; from pathlib import Path; print(hashlib.sha256(Path(sys.argv[1]).read_bytes()).hexdigest().upper())' $requirements
    Assert-Success 'Runtime lock checksum'
    $installedMarker = Join-Path $reviewVenv 'review-requirements.sha256'
    $installedDigest = if (Test-Path -LiteralPath $installedMarker) { (Get-Content -LiteralPath $installedMarker -Raw).Trim() } else { '' }
    if (-not $SkipInstall -and $installedDigest -ne $requirementsDigest) {
        & $reviewPython -m pip --isolated install --require-hashes --only-binary=:all: -r $requirements
        Assert-Success 'Runtime dependency installation'
        & $reviewPython -m pip check
        Assert-Success 'Runtime dependency consistency'
        Set-Content -LiteralPath $installedMarker -Value $requirementsDigest -Encoding ascii
    }

    # This launcher is exclusively for the local, supplied research cohort.
    # It binds only to loopback and never deploys or publishes SMS work.
    $reviewEnvironment = @{
        AUTH_PROVIDER = 'demo'
        AUTH_REQUIRED = 'false'
        SERVE_REACT_UI = 'true'
        PATIENT_DATA_MODE = 'false'
        REAL_PATIENT_DATA = 'false'
        LOCAL_CREDENTIALED_RESEARCH = 'false'
        AZURE_SUPERVISOR_DEMO_MODE = 'false'
        TRUSTED_AUTH_PROXY = 'false'
        SECRETS_PROVIDER = 'env'
        AUDIT_SINK = 'local'
        ALTER_DATA_ROOT = $reviewData
        UHL_CASE_COHORT = 'demo_balanced_100'
        UHL_BALANCED_DEMO_DATA_PATH = (Join-Path $reviewRoot 'data\uhl_demo_balanced_100.json')
        UHL_MODEL_PATH = (Join-Path $reviewRoot 'artifacts\model\uhl_synthetic_acuity_selected.joblib')
        PREWARM_UHL_CACHE_ON_STARTUP = 'false'
        NOTIFICATION_BACKEND = 'sqlite'
        NOTIFICATION_SQLITE_PATH = (Join-Path $reviewData 'processed\notifications.sqlite3')
        SMS_ENABLED = 'false'
        SMS_PUBLISH_ENABLED = 'false'
        ALTER_DISABLE_MULTIAGENT_EXPLANATION = 'true'
        ALLOW_PATIENT_EXPLANATION_DEBUG_ROUTE = 'false'
        WANDB_MODE = 'disabled'
    }
    foreach ($name in $reviewEnvironment.Keys) {
        $priorEnvironment[$name] = [Environment]::GetEnvironmentVariable($name)
        [Environment]::SetEnvironmentVariable($name, $reviewEnvironment[$name])
    }
    Write-Host "Local research review: http://127.0.0.1:$Port/"
    Write-Host 'Select Triage Nurse for checklists; ITD or Researcher for Model Evidence.'
    Write-Host 'Source data/model stay unchanged. Review actions are saved under .local-review-data.'
    Write-Host 'No GitHub push or Azure deployment is performed. Press Ctrl+C to stop.'
    & $reviewPython -m uvicorn app.main:app --host 127.0.0.1 --port $Port
    Assert-Success 'Local web server'
} finally {
    foreach ($name in $priorEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $priorEnvironment[$name])
    }
    Pop-Location
}
