# Build the SongToScore installer.
#   powershell -ExecutionPolicy Bypass -File installer\build.ps1
# Needs: build\cache\python-embed.zip, build\cache\get-pip.py, tools\lilypond-2.24.4, Inno Setup 6
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $root "build\stage"
$cache = Join-Path $root "build\cache"

Write-Host "== stage: $stage"
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Force $stage | Out-Null

# app files
foreach ($f in "app.py", "server.py", "requirements.txt", "README.md", "LICENSE") { Copy-Item (Join-Path $root $f) $stage }
Copy-Item (Join-Path $root "installer\setup_env.ps1") $stage
Copy-Item (Join-Path $root "installer\THIRD_PARTY_NOTICES.txt") $stage
New-Item -ItemType Directory -Force "$stage\songscore", "$stage\web", "$stage\assets", "$stage\tools" | Out-Null
Copy-Item (Join-Path $root "songscore\*.py") "$stage\songscore"
Copy-Item (Join-Path $root "web\*") "$stage\web"
Copy-Item (Join-Path $root "assets\*") "$stage\assets"

# LilyPond
$lily = Get-ChildItem (Join-Path $root "tools") -Directory -Filter "lilypond-*" | Select-Object -First 1
if (-not $lily) { throw "LilyPond not found in tools\" }
Copy-Item $lily.FullName "$stage\tools\" -Recurse

# fast installer tool (uv) used by setup_env.ps1
Copy-Item (Join-Path $cache "uv.exe") "$stage\tools\uv.exe"

# embeddable Python + pip bootstrap
Expand-Archive (Join-Path $cache "python-embed.zip") "$stage\python" -Force
$pth = Get-ChildItem "$stage\python" -Filter "python*._pth" | Select-Object -First 1
(Get-Content $pth.FullName) -replace '^#\s*import site', 'import site' | Set-Content $pth.FullName -Encoding ascii
Copy-Item (Join-Path $cache "get-pip.py") "$stage\python\"

# compile
$iscc = @("$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe", "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 (ISCC.exe) not found" }
& $iscc (Join-Path $PSScriptRoot "SongToScore.iss")
if ($LASTEXITCODE -ne 0) { throw "ISCC failed" }
Get-ChildItem (Join-Path $root "dist") | Select-Object Name, @{n = "MB"; e = { [math]::Round($_.Length / 1MB, 1) } }
