$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:MINI_CC_PYTHON) { $env:MINI_CC_PYTHON } else { "python" }
$OutDir = Join-Path $Repo ".mini_cc\tool-use-eval"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

& $Python -m mini_cc --workspace $Repo --tool-use-eval $OutDir

Write-Host "tool-use eval artifacts: $OutDir"
