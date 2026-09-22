@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "%~dp0requirements.txt" (
  echo ZIP 안에서 설치 파일만 실행한 상태예요.
  echo 창을 닫고 ZIP 전체를 압축 해제한 다음 다시 실행하세요.
  echo 간편 실행판 짱구주머니.exe는 압축 해제나 Python 설치가 필요 없어요.
  pause
  exit /b 1
)
python -c "import sys,tkinter; assert sys.version_info >= (3,11)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 이상과 tkinter가 필요합니다. Python 설치 시 Add Python to PATH를 선택하세요.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo 앱 전용 환경 생성에 실패했습니다.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo 설치 실패. 위 오류와 인터넷 연결을 확인하세요.
  pause
  exit /b 1
)
echo 설치 완료. 실행.bat을 누르세요. AI 기능은 AI설치.bat으로 별도 준비합니다.
pause
