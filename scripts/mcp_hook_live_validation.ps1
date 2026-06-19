$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:MINI_CC_PYTHON) { $env:MINI_CC_PYTHON } else { "python" }
$OutDir = Join-Path $Repo ".mini_cc\mcp-hook-live-3.3"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

& $Python -m mini_cc --workspace $Repo --mcp-hook-live-validation $OutDir

Write-Host "MCP/hook live validation artifacts: $OutDir"
