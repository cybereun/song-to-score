"""새 버전 확인 → 설치 파일 내려받기 → 조용히 설치하고 다시 실행."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import version

_cache: dict = {"at": 0.0, "data": None}
_state: dict = {"state": "idle", "progress": 0.0, "error": "", "path": "", "version": ""}
_lock = threading.Lock()


def _get(url: str, timeout: float = 10):
    req = urllib.request.Request(url, headers={"User-Agent": "SongToScore-Updater",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check(force: bool = False) -> dict:
    """최신 릴리즈 정보. 10분 동안은 저장해 둔 결과를 쓴다."""
    if not force and _cache["data"] and time.time() - _cache["at"] < 600:
        return _cache["data"]
    base = {"current": version.CURRENT, "newer": False}
    try:
        with _get(version.LATEST_URL) as r:
            rel = json.load(r)
    except Exception as exc:
        return {**base, "error": f"업데이트 정보를 가져오지 못했어요 ({exc.__class__.__name__})"}
    latest = str(rel.get("tag_name", "")).lstrip("vV")
    asset = next((a for a in rel.get("assets", []) if str(a.get("name", "")).lower().endswith(".exe")), None)
    data = {
        **base,
        "latest": latest,
        "newer": bool(latest) and version.parse(latest) > version.parse(version.CURRENT),
        "notes": rel.get("body", "") or "",
        "page": rel.get("html_url", version.REPO_URL + "/releases/latest"),
        "published": rel.get("published_at", ""),
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_name": asset.get("name") if asset else None,
        "size": asset.get("size", 0) if asset else 0,
    }
    _cache.update(at=time.time(), data=data)
    return data


def status() -> dict:
    return dict(_state)


def start_download() -> dict:
    info = check()
    if not info.get("newer"):
        return {"ok": False, "error": "이미 최신 버전이에요."}
    if not info.get("asset_url"):
        return {"ok": False, "error": "설치 파일을 찾지 못했어요. 홈페이지에서 직접 받아 주세요."}
    with _lock:
        if _state["state"] in ("downloading",):
            return {"ok": True}
        _state.update(state="downloading", progress=0.0, error="", path="", version=info["latest"])
    threading.Thread(target=_download, args=(info,), daemon=True).start()
    return {"ok": True}


def _download(info: dict) -> None:
    try:
        folder = Path(tempfile.gettempdir()) / "SongToScore-update"
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / (info.get("asset_name") or f"SongToScore_Setup_{info['latest']}.exe")
        part = dest.with_suffix(".part")
        with _get(info["asset_url"], timeout=30) as r:
            total = int(r.headers.get("Content-Length") or info.get("size") or 0)
            done = 0
            with open(part, "wb") as f:
                while chunk := r.read(1 << 20):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        _state["progress"] = round(done / total, 4)
        if total and done != total:
            raise IOError("내려받은 크기가 맞지 않아요")
        part.replace(dest)
        _state.update(state="ready", progress=1.0, path=str(dest))
    except Exception as exc:
        _state.update(state="error", error=f"내려받기 실패: {exc}")


def install_and_restart() -> dict:
    """내려받은 설치 파일을 조용히 실행하고 이 프로그램은 종료한다.
    설치 파일이 끝나면 /RELAUNCH 옵션으로 프로그램을 다시 띄운다."""
    path = _state.get("path")
    if _state.get("state") != "ready" or not path or not Path(path).exists():
        return {"ok": False, "error": "설치 파일이 준비되지 않았어요."}
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH"],
                     creationflags=flags, close_fds=True, cwd=str(Path(path).parent))
    _state["state"] = "installing"
    # 설치 프로그램이 파일을 바꿀 수 있도록 곧바로 종료
    threading.Timer(1.2, lambda: os._exit(0)).start()
    return {"ok": True}
