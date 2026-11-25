@echo off
chcp 65001 >nul
set "VENV=%USERPROFILE%\dev\song-to-score\.venv"
if not exist "%VENV%\Scripts\pythonw.exe" (
  echo 먼저 "설치.bat" 을 실행해 주세요.
  pause
  exit /b 1
)
rem 프로그램 창으로 실행 (브라우저로 쓰려면: python server.py)
start "" "%VENV%\Scripts\pythonw.exe" "%~dp0app.py"
