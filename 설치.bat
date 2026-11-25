@echo off
chcp 65001 >nul
setlocal
title 노래 악보 만들기 - 설치
set "HOME_DIR=%USERPROFILE%\dev\song-to-score"
set "VENV=%HOME_DIR%\.venv"
set "LILY_VER=2.24.4"
set "LILY_URL=https://gitlab.com/api/v4/projects/lilypond%%2Flilypond/packages/generic/lilypond/%LILY_VER%/lilypond-%LILY_VER%-mingw-x86_64.zip"

echo ============================================
echo   노래 악보 만들기 - 처음 한 번만 설치
echo   설치 위치: %HOME_DIR%
echo   (약 5GB, 10~20분 걸릴 수 있어요)
echo ============================================
echo.

if not exist "%HOME_DIR%" mkdir "%HOME_DIR%"

rem --- 1. Python 찾기 (3.10 ~ 3.12)
set "PY="
for %%v in (3.12 3.11 3.10) do (
  if not defined PY (
    py -%%v -c "import sys" >nul 2>&1 && set "PY=py -%%v"
  )
)
if not defined PY (
  echo [오류] Python 3.10~3.12 가 필요해요. https://www.python.org/downloads/ 에서 3.12 를 설치한 뒤 다시 실행하세요.
  pause & exit /b 1
)
echo [1/4] Python: %PY%

rem --- 2. 가상환경
if not exist "%VENV%\Scripts\python.exe" (
  %PY% -m venv "%VENV%" || (echo [오류] 가상환경을 만들지 못했어요 & pause & exit /b 1)
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul

rem --- 3. PyTorch (NVIDIA 그래픽카드가 있으면 GPU 버전)
nvidia-smi >nul 2>&1
if %errorlevel%==0 (
  echo [2/4] NVIDIA GPU 감지 - GPU 버전 PyTorch 설치 중...
  "%VENV%\Scripts\python.exe" -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124 || goto :fail
) else (
  echo [2/4] GPU 없음 - CPU 버전 PyTorch 설치 중... (분석이 느릴 수 있어요)
  "%VENV%\Scripts\python.exe" -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu || goto :fail
)

echo [3/4] 분석 라이브러리 설치 중...
"%VENV%\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt" || goto :fail
"%VENV%\Scripts\python.exe" -m pip install basic-pitch==0.4.0 --no-deps || goto :fail

rem --- 4. LilyPond (악보 조판)
if exist "%HOME_DIR%\tools\lilypond-%LILY_VER%\bin\lilypond.exe" (
  echo [4/4] LilyPond 이미 설치됨
) else (
  echo [4/4] LilyPond %LILY_VER% 내려받는 중... (약 39MB)
  if not exist "%HOME_DIR%\tools" mkdir "%HOME_DIR%\tools"
  powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -Uri '%LILY_URL%' -OutFile '%HOME_DIR%\tools\lilypond.zip'; Expand-Archive -Force '%HOME_DIR%\tools\lilypond.zip' '%HOME_DIR%\tools'; Remove-Item '%HOME_DIR%\tools\lilypond.zip'" || goto :fail
)

echo.
echo 설치 완료! 이제 "실행.bat" 을 더블클릭하세요.
echo (처음 분석할 때 AI 모델(약 2GB)을 한 번 더 내려받아요)
pause
exit /b 0

:fail
echo.
echo [오류] 설치 중 문제가 생겼어요. 인터넷 연결을 확인하고 다시 실행해 주세요.
pause
exit /b 1
