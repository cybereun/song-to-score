"""노래 악보 만들기 — 독립 프로그램 창으로 실행.

로컬 서버를 백그라운드 스레드로 띄우고, Edge WebView2 창에 화면을 연다.
창을 닫으면 프로그램이 완전히 종료된다.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))
TITLE = "노래 악보 만들기"
ICON = APP_DIR / "assets" / "icon.ico"


def _msgbox(text: str, title: str = TITLE, flags: int = 0x40) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, title, flags)


def _single_instance() -> bool:
    ctypes.windll.kernel32.CreateMutexW(None, False, "SongToScore.SingleInstance")
    return ctypes.windll.kernel32.GetLastError() != 183  # ERROR_ALREADY_EXISTS


def _free_port(preferred: int = 8765) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    return preferred


def _env_ready() -> bool:
    installed = (APP_DIR / "python" / "python.exe").exists()
    return not installed or (APP_DIR / "env_ok.flag").exists()


class Api:
    """화면(JavaScript)에서 window.pywebview.api.* 로 부르는 기능."""

    def __init__(self):
        self._window = None  # 밑줄: pywebview가 JS로 노출하지 않도록

    def save_file(self, job_id: str, kind: str):
        import webview
        from server import resolve_file

        try:
            src, _, name = resolve_file(job_id, kind)
        except Exception as exc:
            return {"ok": False, "error": str(getattr(exc, "detail", exc))}
        ext = Path(name).suffix
        res = self._window.create_file_dialog(
            webview.FileDialog.SAVE, directory=str(Path.home() / "Documents"), save_filename=name,
            file_types=(f"{ext[1:].upper()} 파일 (*{ext})", "모든 파일 (*.*)"),
        )
        if not res:
            return {"ok": False, "cancelled": True}
        dest = Path(res if isinstance(res, str) else res[0])
        if not dest.suffix:
            dest = dest.with_suffix(ext)
        shutil.copyfile(src, dest)
        return {"ok": True, "path": str(dest)}

    def open_file(self, job_id: str, kind: str):
        """PDF 등을 기본 프로그램으로 연다 (인쇄용)."""
        from server import resolve_file

        try:
            src, _, _ = resolve_file(job_id, kind)
            os.startfile(str(src))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def show_in_folder(self, path: str):
        import subprocess

        subprocess.Popen(["explorer", "/select,", str(Path(path))])
        return True


def _set_window_icon(window):
    try:
        import clr  # noqa: F401  (pythonnet)
        from System.Drawing import Icon  # type: ignore

        def apply():
            window.native.Icon = Icon(str(ICON))

        window.native.Invoke(__import__("System").Action(apply))
    except Exception:
        pass


def _redirect_output():
    """pythonw(콘솔 없음)에서는 sys.stdout/stderr 가 None 이라 일부 라이브러리가 멈추거나 실패한다.
    로그 파일로 돌려 둔다."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SongToScore"
    log_dir.mkdir(parents=True, exist_ok=True)
    f = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f
    for fd, stream in ((1, sys.stdout), (2, sys.stderr)):
        try:
            os.dup2(f.fileno(), fd)  # 하위 라이브러리(C 코드)의 출력도 파일로
        except OSError:
            pass


def main():
    _redirect_output()
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("SongToScore.App")
        except Exception:
            pass
    if not _single_instance():
        _msgbox("노래 악보 만들기가 이미 실행 중이에요.\n작업 표시줄에서 창을 찾아 주세요.")
        return
    if not _env_ready():
        _msgbox("프로그램 구성 요소가 아직 설치되지 않았어요.\n\n시작 메뉴의 '노래 악보 만들기 - 구성 요소 다시 설치'를 실행해 주세요.",
                flags=0x30)
        return

    # 무거운 라이브러리는 메인 스레드에서 미리 불러 둔다 (창 메시지 루프가 돈 뒤 다른 스레드에서
    # 처음 불러오면 멈추는 경우가 있음)
    import torch  # noqa: F401

    import uvicorn
    import webview

    import server

    port = _free_port()
    config = uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    threading.Thread(target=srv.run, daemon=True).start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(300):  # 최대 30초 기다림 (torch 로딩)
        try:
            urllib.request.urlopen(url + "/api/jobs", timeout=1)
            break
        except Exception:
            time.sleep(0.1)

    api = Api()
    window = webview.create_window(TITLE, url, js_api=api, width=1400, height=900, min_size=(1000, 650),
                                   background_color="#F4F1EA", text_select=True)
    api._window = window
    if ICON.exists():
        window.events.shown += lambda: _set_window_icon(window)
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.start(gui="edgechromium", private_mode=False,
                  storage_path=str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SongToScore" / "webview"))
    srv.should_exit = True


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pythonw 에서는 콘솔이 없으므로 오류를 창으로 보여 준다
        import traceback

        log = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SongToScore" / "error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        _msgbox(f"프로그램을 시작하지 못했어요.\n\n{exc}\n\n자세한 내용: {log}", flags=0x10)
