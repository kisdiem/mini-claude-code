$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Pythonw = if ($env:MINI_CC_PYTHONW) {
    $env:MINI_CC_PYTHONW
} else {
    "C:\Users\sixth\AppData\Local\Programs\Python\Python310\pythonw.exe"
}

Set-Location $Repo
& $Pythonw -m mini_cc.desktop_launcher
