"""경로와 공통 설정.

- 설치판(앱 폴더에 python\\python.exe 가 있음): 작업 파일·모델은 %LOCALAPPDATA%\\SongToScore 에 저장
- 개발판: %USERPROFILE%\\dev\\song-to-score 아래 사용
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
INSTALLED = (APP_DIR / "python" / "python.exe").exists()

if INSTALLED:
    HOME = APP_DIR
    DATA_DIR = Path(os.environ.get("SONGSCORE_DATA",
                                   Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SongToScore"))
else:
    HOME = Path(os.environ.get("SONGSCORE_HOME", Path.home() / "dev" / "song-to-score"))
    DATA_DIR = Path(os.environ.get("SONGSCORE_DATA", HOME))

JOBS_DIR = Path(os.environ.get("SONGSCORE_JOBS", DATA_DIR / "jobs"))
JOBS_DIR.mkdir(parents=True, exist_ok=True)

if INSTALLED:
    # AI 모델을 앱 데이터 폴더에 모아 두어 제거할 때 함께 지울 수 있게 한다
    models = DATA_DIR / "models"
    os.environ.setdefault("HF_HOME", str(models / "huggingface"))
    os.environ.setdefault("TORCH_HOME", str(models / "torch"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

WHISPER_MODEL = os.environ.get("SONGSCORE_WHISPER", "large-v3-turbo")


def find_lilypond() -> str | None:
    for base in (APP_DIR / "tools", HOME / "tools"):
        for exe in sorted(base.glob("lilypond-*/bin/lilypond.exe"), reverse=True):
            return str(exe)
        for exe in sorted(base.glob("lilypond-*/bin/lilypond"), reverse=True):
            return str(exe)
    return shutil.which("lilypond")


def add_cuda_dll_dirs() -> None:
    """faster-whisper(CTranslate2)가 torch에 포함된 cuBLAS/cuDNN DLL을 찾도록 경로 추가."""
    if sys.platform != "win32":
        return
    try:
        import torch  # noqa: F401

        lib = Path(torch.__file__).parent / "lib"
        if lib.is_dir():
            os.add_dll_directory(str(lib))
            os.environ["PATH"] = str(lib) + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass
