@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "SOURCE=%SCRIPT_DIR%..\hybrid_agentic_pension_qwen\models\salary_growth\catboost_m3.cbm"
set "TARGET_DIR=%SCRIPT_DIR%models"
set "TARGET=%TARGET_DIR%\catboost_m3.cbm"

if not exist "%SOURCE%" (
  echo [ERROR] Source model not found:
  echo %SOURCE%
  exit /b 1
)

if not exist "%TARGET_DIR%" mkdir "%TARGET_DIR%"

copy /Y "%SOURCE%" "%TARGET%" >nul
if errorlevel 1 (
  echo [ERROR] Failed to copy model.
  exit /b 1
)

echo [OK] Model copied:
echo %TARGET%
endlocal
