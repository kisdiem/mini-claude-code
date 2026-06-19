param(
    [switch]$Full
)

$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
function Test-PyVersion {
    param([string]$Version)
    try {
        & py.exe "-$Version" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

$Python = if ($env:MINI_CC_PYTHON) {
    $env:MINI_CC_PYTHON
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $preferred = $null
    foreach ($version in @("3.12", "3.11", "3.10")) {
        if (Test-PyVersion $version) {
            $preferred = "py.exe -$version"
            break
        }
    }
    if ($preferred) {
        $preferred
    } else {
        "py.exe -3"
    }
} elseif (Get-Command python.exe -ErrorAction SilentlyContinue) {
    (Get-Command python.exe).Source
} else {
    throw "Python was not found. Install Python 3.10+ or set MINI_CC_PYTHON."
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    if ($Python -like "py.exe -*") {
        $pyArgs = $Python -split " "
        & py.exe $pyArgs[1] @Args
    } else {
        & $Python @Args
    }
}

function Step {
    param([string]$Name)
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
}

Set-Location $Repo
$TempDir = Join-Path $Repo ".tmp-health"
New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
$env:TMP = $TempDir
$env:TEMP = $TempDir
$env:TMPDIR = $TempDir

Step "Python version"
Invoke-Python --version

Step "Import package"
Invoke-Python -c "import mini_cc; print('mini_cc', mini_cc.__version__)"

Step "Compile desktop app"
Invoke-Python -m py_compile mini_cc\desktop_app.py

Step "Mock agent smoke"
Invoke-Python -m mini_cc --mock --s20 --permission auto --workspace $Repo "list files" | Out-Host

Step "Runtime report smoke"
$ReportDir = Join-Path $Repo ".mini_cc\health-runtime-report"
New-Item -ItemType Directory -Force -Path $ReportDir | Out-Null
Invoke-Python -m mini_cc --workspace $Repo --tool-runtime-evidence-smoke --tool-runtime-report $ReportDir | Out-Host

Step "Ignored local state"
if (Get-Command git.exe -ErrorAction SilentlyContinue) {
    git check-ignore .mini_cc\desktop-settings.json .mini_cc\desktop-run.log .env | Out-Host
} else {
    Write-Host "git not found; skipped gitignore verification." -ForegroundColor Yellow
}

if ($Full) {
    Step "Full unit tests"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    Invoke-Python -m unittest discover
} else {
    Write-Host ""
    Write-Host "Full unit tests skipped. Run with -Full to execute python -m unittest discover." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Health check completed." -ForegroundColor Green
