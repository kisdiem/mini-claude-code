@echo off
setlocal
cd /d "%~dp0\.."

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m mini_cc.desktop_launcher
  goto :eof
)

where pythonw >nul 2>nul
if %errorlevel%==0 (
  pythonw -m mini_cc.desktop_launcher
  goto :eof
)

python -m mini_cc.desktop_launcher
