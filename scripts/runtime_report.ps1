$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:MINI_CC_PYTHON) { $env:MINI_CC_PYTHON } else { "python" }
$OutDir = Join-Path $Repo ".mini_cc\tool-runtime-report"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

& $Python -m mini_cc --workspace $Repo --tool-runtime-evidence-smoke --tool-runtime-report $OutDir

Write-Host "runtime report artifacts: $OutDir"
