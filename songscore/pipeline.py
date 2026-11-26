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
    ("separate", "보컬 분리", 0.20),
    ("lyrics", "가사 교차 검증", 0.50),
    ("melody", "멜로디 추출·검증", 0.14),
    ("rhythm", "템포·코드 분석", 0.07),
    ("accomp", "반주 음 받아쓰기", 0.07),
    ("finish", "마무리", 0.02),
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
        lyr = lyrics.transcribe_verified(vocals, src, language, pr.update)
        lyr["verified_lines"] = lyr["lines"]
        original = (_meta(job_dir).get("original_lyrics") or "").strip()
        if original:
            lines = lyrics.align_original(original, lyr["units"])
            if lines:
                lyr.update(mode="original", lines=lines, original=original)
        _free_gpu()

        pr.begin("melody")
        track = melody.track_pitch(vocals, pr.update)
        melody.save_track(track, job_dir / "pitch.npz")
        pr.update(0.6, "다른 방식으로 음 확인 중")
        vocal_notes = accomp.transcribe_accomp(vocals)
        (job_dir / "vocal_notes.json").write_text(json.dumps(vocal_notes), encoding="utf-8")
        notes = build_melody(track, lyr, vocal_notes)
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
        analysis["verify"] = report(analysis)
        (job_dir / "analysis.json").write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        pr.done()
    except Exception as exc:
        traceback.print_exc()
        pr.fail(f"{type(exc).__name__}: {exc}")


def _meta(job_dir: Path) -> dict:
    p = job_dir / "meta.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_melody(track: dict, lyr: dict, vocal_notes: list[dict]) -> list[dict]:
    """음높이 곡선 → 음표. 가사가 있는데 음표가 없는 곳은 다시 확인하고, 두 방식을 비교해 표시한다."""
    from . import melody

    times = [s["t"] for ln in lyr["lines"] for s in ln["syllables"] if s.get("a", True)]
    notes = melody.segment_notes(track, times)
    notes = melody.recover_missing(notes, track, times)
    return melody.cross_check(notes, vocal_notes)


def report(analysis: dict) -> dict:
    """분석 결과 검사표."""
    from .lyrics import sung_without_lyrics

    lyr, notes = analysis["lyrics"], analysis["melody"]
    syl = [s for ln in lyr["lines"] for s in ln["syllables"]]
    holes = [(round(a, 1), round(b, 1)) for a, b in sung_without_lyrics(lyr["lines"], notes) if b - a >= 2.0]
    return {
        "mode": lyr.get("mode", "verified"),
        "syllables": len(syl), "syllables_unsure": sum(not s.get("sure", True) for s in syl),
        "notes": len(notes), "notes_unsure": sum(bool(n.get("u")) for n in notes),
        "recovered": sum(bool(n.get("recovered")) for n in notes),
        "empty_regions": holes,
    }


def apply_original(job_dir: Path, text: str) -> dict:
    """분석이 끝난 곡에 원문 가사를 적용(빈 글이면 받아쓴 가사로 되돌림). 다시 듣지 않아 몇 초면 끝난다."""
    from . import lyrics, melody

    path = job_dir / "analysis.json"
    an = json.loads(path.read_text(encoding="utf-8"))
    lyr = an["lyrics"]
    if "units" not in lyr:
        raise ValueError("예전 방식으로 분석한 곡이에요. 파일을 다시 넣어 분석해 주세요.")
    text = text.strip()
    if text:
        lines = lyrics.align_original(text, lyr["units"])
        if not lines:
            raise ValueError("가사 내용을 찾지 못했어요.")
        lyr.update(mode="original", lines=lines, original=text)
    else:
        lyr.update(mode="verified", lines=lyr["verified_lines"])
        lyr.pop("original", None)
    if (job_dir / "pitch.npz").exists():
        vocal_notes = json.loads((job_dir / "vocal_notes.json").read_text(encoding="utf-8"))
        an["melody"] = build_melody(melody.load_track(job_dir / "pitch.npz"), lyr, vocal_notes)
    an["verify"] = report(an)
    path.write_text(json.dumps(an, ensure_ascii=False), encoding="utf-8")
    meta = _meta(job_dir)
    meta["original_lyrics"] = text
    if meta.get("settings"):  # 예전 가사 수정 내용은 새 가사에 맞지 않으므로 비움
        meta["settings"].update(lyrics_text=None, lyric_edits={}, confirmed=[])
    (job_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return an["verify"]


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
    timeline = [{"n": e.nid, "t0": round(e.t0, 3), "t1": round(e.t1, 3), "lyric": e.lyric, "u": e.u, "lu": e.lu}
                for e in sorted(sc.melody, key=lambda e: e.t0) if e.nid >= 0]
    (out / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    return {
        "pages": len(res["pages"]), "midi": res["midi"], "musicxml": xml, "timeline": timeline,
        "info": {"bpm": sc.bpm, "key": sc.key_label, "bars": sc.n_bars, "time_sig": sc.time_sig,
                 "notes": len(sc.melody), "chords": sum(1 for c in sc.chords if c[2]),
                 "overflow": res["overflow"], "bars_per_line": sc.bars_per_line,
                 "unsure_lyrics": sum(e.lu for e in sc.melody), "unsure_notes": sum(e.u for e in sc.melody)},
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
