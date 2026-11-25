"""faster-whisper로 가사 받아쓰기 + 음절 단위 시간 정보."""
from __future__ import annotations

import re
from pathlib import Path

from .config import WHISPER_MODEL, add_cuda_dll_dirs

HANGUL = re.compile(r"[가-힣]")
TOKEN = re.compile(r"[가-힣]|[A-Za-z0-9'’]+|[぀-ヿ一-鿿]")


def split_syllables(text: str) -> list[str]:
    """한글·한자·가나는 한 글자씩, 영어는 단어 하나씩 나눈다."""
    return TOKEN.findall(text)


def _load_model():
    add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    try:
        import torch

        if torch.cuda.is_available():
            return WhisperModel(WHISPER_MODEL, device="cuda", compute_type="float16")
    except Exception:
        pass
    return WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")


def transcribe(vocals: Path, language: str | None = None, progress=lambda p, msg="": None) -> dict:
    try:
        model = _load_model()
    except Exception:
        # GPU 라이브러리 문제 시 CPU로 재시도
        from faster_whisper import WhisperModel

        model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")

    import librosa

    audio, _ = librosa.load(str(vocals), sr=16000, mono=True)
    segments, info = model.transcribe(
        audio,
        language=language or None,
        word_timestamps=True,
        # 음성 구간 감지(VAD)는 노래를 말소리로 못 알아보고 통째로 잘라 내는 경우가 많아 끈다.
        # 대신 무음 구간의 헛인식은 hallucination_silence_threshold 와 멜로디 확인으로 거른다.
        vad_filter=False,
        beam_size=5,
        condition_on_previous_text=False,
        no_speech_threshold=0.8,
        hallucination_silence_threshold=2.0,
    )
    total = max(info.duration, 1e-6)
    lines = []
    for seg in segments:
        words = []
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append({"start": float(w.start), "end": float(w.end), "text": text})
        lines += group_lines(words)
        progress(min(0.99, seg.end / total), "가사 인식 중")
    del model
    return {"language": info.language, "lines": lines}


FAKE_PHRASES = ("다음 영상", "시청해", "구독", "좋아요", "감사합니다", "자막", "Thank you for watching",
                "Subscribe")


def drop_unsung(result: dict, notes: list[dict]) -> dict:
    """멜로디 음표가 거의 없는 구간의 문장(무음에서 생긴 헛인식)을 버린다."""
    keep = []
    for ln in result["lines"]:
        inside = sum(1 for n in notes if n["end"] > ln["start"] and n["start"] < ln["end"])
        fake = any(p in ln["text"] for p in FAKE_PHRASES)
        if inside >= max(1, len(ln["syllables"]) // 4) and not (fake and inside < len(ln["syllables"])):
            keep.append(ln)
    return {**result, "lines": keep}


def group_lines(words: list[dict], gap: float = 0.6, max_syl: int = 18) -> list[dict]:
    """쉬는 구간(gap 초 이상)이나 너무 긴 줄에서 가사 줄을 나눈다."""
    groups: list[list[dict]] = []
    cur: list[dict] = []
    n_syl = 0
    for w in words:
        k = len(split_syllables(w["text"]))
        if cur and (w["start"] - cur[-1]["end"] >= gap or n_syl + k > max_syl):
            groups.append(cur)
            cur, n_syl = [], 0
        cur.append(w)
        n_syl += k
    if cur:
        groups.append(cur)
    out = []
    for g in groups:
        syl = syllable_times(g)
        if not syl:
            continue
        text = "".join(
            (" " if i and not (g[i]["text"][:1] in ".,!?") else "") + w["text"] for i, w in enumerate(g)
        ).strip()
        out.append({"start": g[0]["start"], "end": g[-1]["end"], "text": text, "syllables": syl})
    return out


def syllable_times(words: list[dict]) -> list[dict]:
    """단어 시간 구간을 음절 수만큼 고르게 나눠 음절별 시작 시간을 만든다."""
    out = []
    for w in words:
        syl = split_syllables(w["text"])
        if not syl:
            continue
        dur = max(w["end"] - w["start"], 0.05)
        step = dur / len(syl)
        for i, s in enumerate(syl):
            out.append({"t": round(w["start"] + i * step, 3), "text": s})
    return out
