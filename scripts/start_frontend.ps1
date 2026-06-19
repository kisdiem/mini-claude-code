$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Python = if ($env:MINI_CC_PYTHON) { $env:MINI_CC_PYTHON } else { "python" }
$Port = if ($env:MINI_CC_WEB_PORT) { $env:MINI_CC_WEB_PORT } else { "8765" }

Set-Location $Repo
& $Python -m mini_cc.web_server $Port
