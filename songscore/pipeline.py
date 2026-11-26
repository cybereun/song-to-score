"""전체 분석 파이프라인과 악보 렌더링."""
from __future__ import annotations

import gc
import json
import shutil
import time
import traceback
from pathlib import Path

import numpy as np

STEPS = [  # (키, 이름, 비중)
    ("separate", "보컬 분리", 0.30),
    ("lyrics", "가사 인식", 0.25),
    ("melody", "멜로디 추출", 0.15),
    ("rhythm", "템포·코드 분석", 0.10),
    ("accomp", "반주 음 받아쓰기", 0.15),
    ("finish", "마무리", 0.05),
]


class Progress:
    def __init__(self, job_dir: Path):
        self.path = job_dir / "status.json"
        self.state = {"state": "running", "step": "", "step_name": "", "progress": 0.0,
                      "message": "", "steps": [s[0] for s in STEPS], "started": time.time()}
        self.base = 0.0
        self.weight = 0.0
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def begin(self, key: str):
        base = 0.0
        for k, name, w in STEPS:
            if k == key:
                self.base, self.weight = base, w
                self.state.update(step=k, step_name=name, message=name + " 중")
                break
            base += w
        self.update(0.0)

    def update(self, frac: float, msg: str = ""):
        self.state["progress"] = round(min(1.0, self.base + self.weight * frac), 4)
        if msg:
            self.state["message"] = msg
        self.save()

    def done(self):
        self.state.update(state="done", progress=1.0, message="분석 완료", step="finish")
        self.save()

    def fail(self, err: str):
        self.state.update(state="error", message=err)
        self.save()


def _free_gpu():
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def analyze(job_dir: Path, src: Path, language: str | None = None) -> None:
    from . import accomp, harmony, lyrics, melody, rhythm, separate

    pr = Progress(job_dir)
    try:
        pr.begin("separate")
        vocals, acc = job_dir / "vocals.wav", job_dir / "accomp.wav"
        if not (vocals.exists() and acc.exists()):
            vocals, acc = separate.separate(src, job_dir, pr.update)
        _free_gpu()

        pr.begin("lyrics")
        lyr = lyrics.transcribe(vocals, language, pr.update)
        _free_gpu()

        pr.begin("melody")
        track = melody.track_pitch(vocals, pr.update)
        splits = [s["t"] for ln in lyr["lines"] for s in ln["syllables"]]
        notes = melody.segment_notes(track, splits)
        # 노래는 있는데 가사가 비어 있는 구간(주로 곡 첫 부분)을 다시 받아써서 채운다
        refilled = lyrics.fill_missing(vocals, lyr, notes, language, pr.update)
        if refilled is not lyr:
            lyr = refilled
            splits = [s["t"] for ln in lyr["lines"] for s in ln["syllables"]]
            notes = melody.segment_notes(track, splits)
        lyr = lyrics.drop_unsung(lyr, notes)
        _free_gpu()

        pr.begin("rhythm")
        rh = rhythm.analyze(src, acc, pr.update)

        pr.begin("accomp")
        acc_notes = accomp.transcribe_accomp(acc, pr.update)

        pr.begin("finish")
        pcs = np.zeros(12)
        for n in notes:
            pcs[n["midi"] % 12] += n["end"] - n["start"]
        key = harmony.estimate_key(np.asarray(rh["global_chroma"]), pcs)
        analysis = {**rh, "melody": notes, "lyrics": lyr, "accomp_notes": acc_notes, "key": key,
                    "source": src.name}
        (job_dir / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        pr.done()
    except Exception as exc:
        traceback.print_exc()
        pr.fail(f"{type(exc).__name__}: {exc}")


def render(job_dir: Path, settings: dict) -> dict:
    from .arrange import build_score
    from .export import write_musicxml
    from .lily import render as lily_render

    analysis = json.loads((job_dir / "analysis.json").read_text(encoding="utf-8"))
    sc = build_score(analysis, settings)
    out = job_dir / "out"
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    res = lily_render(sc, out, "score")
    xml = None
    try:
        xml = write_musicxml(sc, out / "score.musicxml")
    except Exception:
        traceback.print_exc()
    # 재생 표시용: 악보의 멜로디 음표 ↔ 원곡 시각
    timeline = [{"n": e.nid, "t0": round(e.t0, 3), "t1": round(e.t1, 3), "lyric": e.lyric}
                for e in sorted(sc.melody, key=lambda e: e.t0) if e.nid >= 0]
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    return {
        "pages": len(res["pages"]), "midi": res["midi"], "musicxml": xml, "timeline": timeline,
        "info": {"bpm": sc.bpm, "key": sc.key_label, "bars": sc.n_bars, "time_sig": sc.time_sig,
                 "notes": len(sc.melody), "chords": sum(1 for c in sc.chords if c[2])},
    }


def ensure_pdf(job_dir: Path) -> Path:
    from .lily import make_pdf

    return make_pdf(job_dir / "out", "score")


def update_settings(job_dir: Path, settings: dict) -> None:
    """가사만 고쳤을 때: 화면(SVG)은 브라우저에서 바로 고치고, 여기서는 원고(.ly)만 새로 써 둔다.
    PDF·MIDI·MusicXML 은 저장할 때 새 원고로 다시 만든다."""
    from .arrange import build_score
    from .lily import build_ly

    analysis = json.loads((job_dir / "analysis.json").read_text(encoding="utf-8"))
    sc = build_score(analysis, settings)
    out = job_dir / "out"
    out.mkdir(exist_ok=True)
    (out / "score.ly").write_text(build_ly(sc), encoding="utf-8")
    for name in ("score.pdf", "score.musicxml", "score.midi", "score.mid"):
        (out / name).unlink(missing_ok=True)


def ensure_musicxml(job_dir: Path, settings: dict) -> Path:
    from .arrange import build_score
    from .export import write_musicxml

    path = job_dir / "out" / "score.musicxml"
    if not path.exists():
        analysis = json.loads((job_dir / "analysis.json").read_text(encoding="utf-8"))
        write_musicxml(build_score(analysis, settings), path)
    return path
