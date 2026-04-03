@echo off
setlocal

for %%I in ("%~dp0.") do set "CARD_ROOT=%%~fI"
set "SCRIPT_PATH=%~dp0tools\NomadScreen-RefreshMetadata.ps1"

if not exist "%SCRIPT_PATH%" (
  echo Could not find "%SCRIPT_PATH%".
  echo Make sure the full sdcard-template contents were copied to the SD card.
  pause
  exit /b 1
)

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_PATH%" -CardRoot "%CARD_ROOT%"
set EXIT_CODE=%ERRORLEVEL%

echo.
if %EXIT_CODE% NEQ 0 (
  echo Metadata refresh failed. See the messages above for details.
) else (
  echo Metadata refresh complete. You can eject the SD card after this window closes.
)

pause
exit /b %EXIT_CODE%
