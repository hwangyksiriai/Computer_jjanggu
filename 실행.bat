@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0app.py"
  exit /b 0
)
python -c "import bootstrap,tkinter,PIL,pypdf,tkinterdnd2,pymupdf,sounddevice" >nul 2>&1
if errorlevel 1 (
  echo 필요한 구성 요소를 설치합니다...
  python -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 설치를 완료하지 못했습니다. Python 설치를 확인해 주세요.
    pause
    exit /b 1
  )
)
start "" pythonw "%~dp0app.py"
