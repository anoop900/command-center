@echo off
rem Double-click to install Command Center. Runs bootstrap\install.ps1 with the script policy relaxed for this one run only.
setlocal
cd /d "%~dp0"
if not exist "%~dp0bootstrap\install.ps1" (
  echo.
  echo This looks like it was opened from inside the zip file.
  echo Close this window, right-click the zip file, choose Extract All,
  echo then open the extracted folder and double-click this file again.
  echo.
  pause
  exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0bootstrap\install.ps1" %*
set "CODE=%ERRORLEVEL%"
echo.
if not "%CODE%"=="0" echo The installer stopped. Please read the message above, then close this window.
pause
exit /b %CODE%
