$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:MINI_CC_PYTHON) { $env:MINI_CC_PYTHON } else { "python" }
$OutDir = Join-Path $Repo ".mini_cc\demo"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

& $Python -m mini_cc --mock --s20 --permission auto --workspace $Repo "s20 snapshot" |
    Tee-Object -FilePath (Join-Path $OutDir "mock-demo.txt")

Write-Host "mock demo artifact: $OutDir\mock-demo.txt"
