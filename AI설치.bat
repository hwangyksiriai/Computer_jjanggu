@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 먼저 처음설치.bat을 실행하세요.
  pause
  exit /b 1
)
node -e "if(Number(process.versions.node.split('.')[0]) < 22) process.exit(1)" >nul 2>&1
if errorlevel 1 (
  echo Node.js 22 이상을 설치하고 다시 실행하세요.
  pause
  exit /b 1
)
echo 앱을 종료한 상태에서 설치하세요. AI 모델을 인터넷에서 내려받습니다.
call npm ci
if errorlevel 1 (
  echo Node 구성 요소 설치 실패. 인터넷 연결과 npm 설치를 확인하세요.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" setup_runtime.py
if errorlevel 1 (
  echo 모델 준비 실패. 오류 내용과 디스크 공간을 확인하세요.
  pause
  exit /b 1
)
echo AI 준비 완료. 실행.bat을 실행하세요.
pause
