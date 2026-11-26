# 노래 악보 만들기 - 구성 요소 설치 (설치 프로그램이 실행, 시작 메뉴에서 다시 실행 가능)
# 빠른 설치 도구 uv 로 설치하고, 문제가 생기면 pip 로 자동 전환한다.
param([string]$AppDir = $PSScriptRoot)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Host.UI.RawUI.WindowTitle = "노래 악보 만들기 - 구성 요소 설치"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$py = Join-Path $AppDir "python\python.exe"
$uv = Join-Path $AppDir "tools\uv.exe"
$flag = Join-Path $AppDir "env_ok.flag"
$log = Join-Path $AppDir "setup_log.txt"
Remove-Item $flag -ErrorAction SilentlyContinue
Start-Transcript -Path $log -Force | Out-Null

# uv: 내려받은 파일을 캐시에 남기지 않고(디스크 절약), Python 을 따로 받지 않음
$env:UV_NO_CACHE = "1"
$env:UV_PYTHON_DOWNLOADS = "never"
$env:UV_CONCURRENT_DOWNLOADS = "8"
$script:useUv = Test-Path $uv
$script:pipReady = $false

function Step($n, $text) { Write-Host ""; Write-Host "[$n/4] $text" -ForegroundColor Cyan }
function Elapsed($sw) { "{0}분 {1}초" -f [int][math]::Floor($sw.Elapsed.TotalMinutes), $sw.Elapsed.Seconds }

function Ensure-Pip {
    if ($script:pipReady) { return }
    $ErrorActionPreference = 'Continue'
    Write-Host "  (기본 설치 도구 pip 준비 중)"
    & $py (Join-Path $AppDir "python\get-pip.py") --no-warn-script-location --disable-pip-version-check -q
    if ($LASTEXITCODE -ne 0) { throw "pip 준비 실패" }
    $script:pipReady = $true
}

function Install {
    # uv·pip 는 진행 메시지를 stderr 로 내보내서, 'Stop' 이면 PowerShell 5.1 이 오류로 착각할 수 있다
    $ErrorActionPreference = 'Continue'
    if ($script:useUv) {
        & $uv pip install --python $py @args
        if ($LASTEXITCODE -eq 0) { return }
        Write-Host "  빠른 설치가 실패해서 기본 방식(pip)으로 다시 시도해요..." -ForegroundColor Yellow
        $script:useUv = $false
    }
    Ensure-Pip
    # --no-compile: 설치 때 .pyc 미리 만들기를 건너뛰어 시간 단축 (필요할 때 자동 생성)
    & $py -m pip install --no-warn-script-location --disable-pip-version-check --no-compile @args
    if ($LASTEXITCODE -ne 0) { throw "라이브러리 설치 실패: $args" }
}

Write-Host "============================================================"
Write-Host "  노래 악보 만들기 - 구성 요소 설치"
Write-Host "  분석 라이브러리를 인터넷에서 내려받아요 (약 3~4GB)."
Write-Host "  인터넷 속도에 따라 3~15분쯤 걸려요. 창을 닫지 마세요."
Write-Host "============================================================"

$total = [Diagnostics.Stopwatch]::StartNew()
try {
    $gpu = $false
    try { & nvidia-smi -L *> $null; $gpu = ($LASTEXITCODE -eq 0) } catch { $gpu = $false }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    if ($gpu) {
        Step 1 "NVIDIA 그래픽카드 감지 - GPU용 PyTorch 설치 (약 2.5GB)"
        Install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cu124
    } else {
        Step 1 "GPU 없음 - CPU용 PyTorch 설치 (분석이 느릴 수 있어요)"
        Install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
    }
    Write-Host "  완료 ($(Elapsed $sw))"

    Step 2 "분석 라이브러리 설치"
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Install -r (Join-Path $AppDir "requirements.txt")
    Install basic-pitch==0.4.0 --no-deps
    Write-Host "  완료 ($(Elapsed $sw))"

    Step 3 "설치 확인"
    $ErrorActionPreference = 'Continue'  # 라이브러리 경고 메시지(stderr)로 멈추지 않게
    & $py -c "import torch, demucs, faster_whisper, librosa, torchcrepe, music21, webview, fastapi, basic_pitch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available())"
    if ($LASTEXITCODE -ne 0) { throw "설치 확인 실패" }

    Step 4 "마무리"
    Set-Content -Path $flag -Value (Get-Date -Format s)
    Write-Host ""
    Write-Host "설치가 끝났어요! (걸린 시간 $(Elapsed $total))" -ForegroundColor Green
    Write-Host "바탕화면의 '노래 악보 만들기'를 실행하세요."
    Write-Host "(처음 곡을 분석할 때 분석 모델 약 2GB를 한 번 더 내려받아요)"
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
