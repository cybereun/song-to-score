"""노래 → 악보 로컬 서버. 실행: python server.py  (http://127.0.0.1:8765)"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
import webbrowser
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
from songscore import pipeline  # noqa: E402
from songscore.arrange import MODES  # noqa: E402
from songscore.config import APP_DIR, JOBS_DIR, find_lilypond  # noqa: E402

PORT = int(os.environ.get("SONGSCORE_PORT", "8765"))
if "--port" in sys.argv:
    PORT = int(sys.argv[sys.argv.index("--port") + 1])
app = FastAPI(title="노래 악보 만들기")
gpu_lock = threading.Lock()
render_lock = threading.Lock()
ALLOWED = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}


def job_dir(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", job_id):
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    d = JOBS_DIR / job_id
    if not d.is_dir():
        raise HTTPException(404, "작업을 찾을 수 없습니다")
    return d


def _run(d: Path, src: Path, language: str | None):
    (d / "status.json").write_text(json.dumps({"state": "waiting", "progress": 0, "message": "앞 작업이 끝나기를 기다리는 중"},
                                              ensure_ascii=False), encoding="utf-8")
    with gpu_lock:
        pipeline.analyze(d, src, language)


@app.post("/api/jobs")
async def create_job(file: UploadFile = File(...), language: str = Form("auto"),
                     original_lyrics: str = Form("")):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(400, "mp3, wav, m4a, flac, ogg 파일만 지원합니다")
    job_id = uuid.uuid4().hex[:12]
    d = JOBS_DIR / job_id
    d.mkdir(parents=True)
    src = d / f"source{ext}"
    with src.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)
    meta = {"id": job_id, "name": Path(file.filename).stem, "created": time.time(), "language": language,
            "original_lyrics": original_lyrics[:20000]}
    (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    lang = None if language == "auto" else language
    threading.Thread(target=_run, args=(d, src, lang), daemon=True).start()
    return meta


@app.get("/api/jobs")
def list_jobs():
    out = []
    for d in sorted(JOBS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        m, s = d / "meta.json", d / "status.json"
        if not m.exists():
            continue
        meta = json.loads(m.read_text(encoding="utf-8"))
        st = json.loads(s.read_text(encoding="utf-8")) if s.exists() else {}
        meta["state"] = st.get("state", "unknown")
        out.append(meta)
        if len(out) >= 20:
            break
    return out


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    """최근 작업 지우기: 그 곡의 분석 결과·분리한 음원·악보를 지운다 (원래 음원 파일은 그대로)."""
    import shutil

    d = job_dir(job_id)
    st = d / "status.json"
    if st.exists() and '"running"' in st.read_text(encoding="utf-8"):
        raise HTTPException(409, "분석 중인 작업은 지울 수 없어요")
    with render_lock:
        shutil.rmtree(d, ignore_errors=True)
    if d.exists():
        raise HTTPException(500, "일부 파일을 지우지 못했어요. 프로그램을 다시 열고 시도해 주세요.")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/status")
def status(job_id: str):
    s = job_dir(job_id) / "status.json"
    if not s.exists():
        return {"state": "waiting", "progress": 0, "message": "준비 중"}
    try:
        return json.loads(s.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"state": "running", "progress": 0, "message": "진행 중"}


@app.get("/api/jobs/{job_id}/analysis")
def analysis(job_id: str):
    d = job_dir(job_id)
    a = d / "analysis.json"
    if not a.exists():
        raise HTTPException(409, "아직 분석 중입니다")
    an = json.loads(a.read_text(encoding="utf-8"))
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    return {
        "id": job_id, "name": meta.get("name", ""), "duration": an["duration"], "tempo": an["tempo"],
        "key": an["key"], "language": an["lyrics"].get("language"),
        "lyrics": [ln["text"] for ln in an["lyrics"]["lines"]],
        "melody_notes": len(an["melody"]), "accomp_notes": len(an.get("accomp_notes", [])),
        "modes": MODES, "settings": meta.get("settings"),
        "verify": an.get("verify"), "original_lyrics": meta.get("original_lyrics", ""),
        "can_original": "units" in an["lyrics"],
    }


@app.post("/api/jobs/{job_id}/original")
def set_original(job_id: str, body: dict):
    """원문 가사 적용(빈 글이면 받아쓴 가사로 되돌림)."""
    d = job_dir(job_id)
    if not (d / "analysis.json").exists():
        raise HTTPException(409, "아직 분석 중입니다")
    with render_lock:
        try:
            verify = pipeline.apply_original(d, str(body.get("text", ""))[:20000])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    return {"ok": True, "verify": verify}


@app.post("/api/jobs/{job_id}/render")
def render(job_id: str, settings: dict):
    d = job_dir(job_id)
    if not (d / "analysis.json").exists():
        raise HTTPException(409, "아직 분석 중입니다")
    with render_lock:
        try:
            res = pipeline.render(d, settings)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    meta_p = d / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["settings"] = settings
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    stamp = int(time.time() * 1000)
    files = {"pdf": f"/api/jobs/{job_id}/file/pdf?t={stamp}"}
    if res["midi"]:
        files["midi"] = f"/api/jobs/{job_id}/file/midi?t={stamp}"
    if res["musicxml"]:
        files["musicxml"] = f"/api/jobs/{job_id}/file/musicxml?t={stamp}"
    pages = [f"/api/jobs/{job_id}/page/{i + 1}?t={stamp}" for i in range(res["pages"])]
    return {"info": res["info"], "files": files, "pages": pages, "timeline": res["timeline"]}


@app.get("/api/version")
def app_version():
    from songscore import version
    from songscore.config import INSTALLED

    return {"version": version.CURRENT, "installed": INSTALLED, "developer": version.DEVELOPER,
            "homepage": version.HOMEPAGE, "repo": version.REPO_URL, "threads": version.THREADS}


@app.get("/api/update/check")
def update_check(force: int = 0):
    from songscore import updater

    return updater.check(force=bool(force))


@app.post("/api/update/start")
def update_start():
    from songscore import updater
    from songscore.config import INSTALLED

    if not INSTALLED:
        return JSONResponse({"ok": False, "error": "개발용 실행에서는 자동 업데이트를 할 수 없어요. 홈페이지에서 받아 주세요."},
                            status_code=400)
    return updater.start_download()


@app.get("/api/update/status")
def update_status():
    from songscore import updater

    return updater.status()


@app.post("/api/update/install")
def update_install():
    from songscore import updater
    from songscore.config import INSTALLED

    if not INSTALLED:
        return JSONResponse({"ok": False, "error": "설치판에서만 쓸 수 있어요."}, status_code=400)
    return updater.install_and_restart()


@app.post("/api/jobs/{job_id}/settings")
def save_settings(job_id: str, settings: dict):
    """가사 고치기처럼 화면을 다시 그릴 필요가 없는 변경을 저장한다."""
    d = job_dir(job_id)
    with render_lock:
        try:
            pipeline.update_settings(d, settings)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)
    meta_p = d / "meta.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8"))
    meta["settings"] = settings
    meta_p.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return {"ok": True}


@app.get("/api/jobs/{job_id}/page/{n}")
def page(job_id: str, n: int):
    out = job_dir(job_id) / "out"
    p = out / f"score-{n}.svg"
    if not p.exists() and n == 1:
        p = out / "score.svg"
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


FILES = {
    "pdf": ("out/score.pdf", "application/pdf", ".pdf"),
    "midi": ("out/score.midi", "audio/midi", ".mid"),
    "musicxml": ("out/score.musicxml", "application/vnd.recordare.musicxml+xml", ".musicxml"),
    "vocals": ("vocals.wav", "audio/wav", "_보컬.wav"),
    "accomp": ("accomp.wav", "audio/wav", "_반주.wav"),
}


def resolve_file(job_id: str, kind: str) -> tuple[Path, str, str]:
    """(파일 경로, MIME, 저장용 파일 이름)"""
    d = job_dir(job_id)
    if kind == "source":
        src = next(d.glob("source.*"), None)
        if not src:
            raise HTTPException(404)
        return src, "application/octet-stream", src.name
    if kind not in FILES:
        raise HTTPException(404)
    rel, mime, ext = FILES[kind]
    if kind in ("pdf", "midi", "musicxml"):
        try:
            with render_lock:  # PDF·MIDI·MusicXML 은 저장·인쇄할 때 최신 원고로 만든다
                if kind == "musicxml":
                    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                    pipeline.ensure_musicxml(d, meta.get("settings") or {})
                else:
                    pipeline.ensure_pdf(d)
        except Exception as exc:
            raise HTTPException(500, str(exc))
    p = d / rel
    if kind == "midi" and not p.exists():
        p = d / "out/score.mid"
    if not p.exists():
        raise HTTPException(404, "파일이 없습니다")
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    title = (meta.get("settings") or {}).get("title") or meta.get("name") or "score"
    mode = (meta.get("settings") or {}).get("mode", "")
    label = {"lead": "_멜로디", "piano_easy": "_피아노", "piano_full": "_피아노", "guitar_melody": "_기타TAB",
             "guitar_chords": "_기타코드"}.get(mode, "") if kind in ("pdf", "midi", "musicxml") else ""
    name = re.sub(r'[\\/:*?"<>|]', "_", title) + label + ext
    return p, mime, name


@app.get("/api/jobs/{job_id}/file/{kind}")
def get_file(job_id: str, kind: str, download: int = 0):
    p, mime, name = resolve_file(job_id, kind)
    if kind == "source":
        return FileResponse(p)
    return FileResponse(p, media_type=mime, filename=name if download else None,
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/health")
def health():
    import torch

    return {"lilypond": bool(find_lilypond()), "cuda": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """화면 파일은 매번 새로 확인하게 한다 (업데이트 뒤에도 예전 화면이 남지 않도록)."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def index():
    """화면 파일 주소에 버전을 붙여(style.css?v=1.0.1) 버전이 바뀌면 새로 받게 한다."""
    from fastapi.responses import HTMLResponse
    from songscore import version

    html = (APP_DIR / "web" / "index.html").read_text(encoding="utf-8").replace("__V__", version.VERSION)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=APP_DIR / "web", html=True), name="web")

if __name__ == "__main__":
    import uvicorn

    if "--no-browser" not in sys.argv:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
