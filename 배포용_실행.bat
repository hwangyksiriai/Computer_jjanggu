@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo 먼저 처음설치.bat을 실행하세요.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" "%~dp0desktop_entry.py"
