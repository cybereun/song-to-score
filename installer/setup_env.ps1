# 노래 악보 만들기 - 구성 요소 설치 (설치 프로그램이 실행, 시작 메뉴에서 다시 실행 가능)
param([string]$AppDir = $PSScriptRoot)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "노래 악보 만들기 - 구성 요소 설치"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$py = Join-Path $AppDir "python\python.exe"
$flag = Join-Path $AppDir "env_ok.flag"
$log = Join-Path $AppDir "setup_log.txt"
Remove-Item $flag -ErrorAction SilentlyContinue
Start-Transcript -Path $log -Force | Out-Null

function Step($n, $text) { Write-Host ""; Write-Host "[$n/5] $text" -ForegroundColor Cyan }
function Pip { & $py -m pip install --no-warn-script-location --disable-pip-version-check @args; if ($LASTEXITCODE -ne 0) { throw "pip install 실패: $args" } }

Write-Host "============================================================"
Write-Host "  노래 악보 만들기 - 구성 요소 설치"
Write-Host "  AI 분석 라이브러리를 인터넷에서 내려받아요 (약 3~5GB)."
Write-Host "  인터넷 속도에 따라 10~30분 걸릴 수 있어요. 창을 닫지 마세요."
Write-Host "============================================================"

try {
    Step 1 "라이브러리 설치 도구 준비"
    & $py (Join-Path $AppDir "python\get-pip.py") --no-warn-script-location --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "pip 준비 실패" }

    $gpu = $false
    try { & nvidia-smi -L *> $null; $gpu = ($LASTEXITCODE -eq 0) } catch { $gpu = $false }
    if ($gpu) {
        Step 2 "NVIDIA 그래픽카드 감지 - GPU용 PyTorch 설치 (약 2.5GB)"
        Pip torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
    } else {
        Step 2 "GPU 없음 - CPU용 PyTorch 설치 (분석이 느릴 수 있어요)"
        Pip torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    }

    Step 3 "분석 라이브러리 설치"
    Pip -r (Join-Path $AppDir "requirements.txt")
    Pip basic-pitch==0.4.0 --no-deps

    Step 4 "설치 확인"
    & $py -c "import torch, demucs, faster_whisper, librosa, torchcrepe, music21, webview, fastapi, basic_pitch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
    if ($LASTEXITCODE -ne 0) { throw "설치 확인 실패" }

    Step 5 "마무리"
    Set-Content -Path $flag -Value (Get-Date -Format s)
    Write-Host ""
    Write-Host "설치가 끝났어요! 바탕화면의 '노래 악보 만들기'를 실행하세요." -ForegroundColor Green
    Write-Host "(처음 곡을 분석할 때 AI 모델 약 2GB를 한 번 더 내려받아요)"
    Stop-Transcript | Out-Null
    Start-Sleep -Seconds 4
    exit 0
}
catch {
    Write-Host ""
    Write-Host "설치 중 문제가 생겼어요: $_" -ForegroundColor Red
    Write-Host "인터넷 연결을 확인한 뒤, 시작 메뉴의 '노래 악보 만들기 - 구성 요소 다시 설치'를 실행해 주세요."
    Write-Host "기록 파일: $log"
    Stop-Transcript | Out-Null
    Read-Host "Enter 키를 누르면 창이 닫혀요"
    exit 1
}
