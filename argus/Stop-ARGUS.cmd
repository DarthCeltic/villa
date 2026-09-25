@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Stop-ARGUS.ps1" %*
if errorlevel 1 (
  echo.
  echo ARGUS did not stop cleanly. The message above explains what needs attention.
  pause
  exit /b 1
)
endlocal
