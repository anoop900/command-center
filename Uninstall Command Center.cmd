@echo off
rem Double-click to remove Command Center. Runs bootstrap\uninstall.ps1 with the script policy relaxed for this one run only.
rem It asks before each part, and keeps your data unless you choose to delete it.
setlocal
cd /d "%~dp0"
if not exist "%~dp0bootstrap\uninstall.ps1" (
  echo.
  echo This looks like it was opened from inside the zip file.
  echo Close this window, right-click the zip file, choose Extract All,
  echo then open the extracted folder and double-click this file again.
  echo.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap\uninstall.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
pause
exit /b %CODE%
