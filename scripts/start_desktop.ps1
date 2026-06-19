$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent $PSScriptRoot
$Pythonw = if ($env:MINI_CC_PYTHONW) {
    $env:MINI_CC_PYTHONW
} elseif (Get-Command pythonw.exe -ErrorAction SilentlyContinue) {
    (Get-Command pythonw.exe).Source
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    "py.exe"
} else {
    "python.exe"
}

Set-Location $Repo
if ((Split-Path $Pythonw -Leaf) -ieq "py.exe") {
    & $Pythonw -3 -m mini_cc.desktop_launcher
} else {
    & $Pythonw -m mini_cc.desktop_launcher
}
