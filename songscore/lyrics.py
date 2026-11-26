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


def _is_real_model(d: Path) -> bool:
    f = d / "model.bin"
    return f.exists() and not f.is_symlink() and f.stat().st_size > 50_000_000


def model_path(progress=lambda p, msg="": None) -> str:
    """가사 인식 모델을 '바로가기(심볼릭 링크) 없는 일반 파일'로 받아 둔 폴더 경로.

    Hugging Face 캐시는 모델 파일을 바로가기로 연결해 두는데, Windows 에서는
    CTranslate2 가 그 바로가기를 열지 못하는 경우가 있다(Unable to open file 'model.bin').
    """
    from faster_whisper.utils import download_model

    from .config import DATA_DIR

    d = DATA_DIR / "models" / f"whisper-{WHISPER_MODEL}"
    if _is_real_model(d):
        return str(d)
    progress(0.02, "가사 인식 모델 내려받는 중 (처음 한 번, 약 1.6GB)")
    download_model(WHISPER_MODEL, output_dir=str(d))
    # 혹시 바로가기로 받아졌다면 실제 파일로 바꿔 둔다
    for f in d.iterdir():
        if f.is_symlink():
            target = f.resolve()
            f.unlink()
            import shutil

            shutil.copyfile(target, f)
    if not _is_real_model(d):
        raise RuntimeError(f"가사 인식 모델을 받지 못했어요. 인터넷 연결을 확인해 주세요. ({d})")
    return str(d)


def _load_model(path: str):
    add_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    try:
        import torch

        if torch.cuda.is_available():
            return WhisperModel(path, device="cuda", compute_type="float16")
    except Exception:
        pass
    return WhisperModel(path, device="cpu", compute_type="int8")


def transcribe(vocals: Path, language: str | None = None, progress=lambda p, msg="": None) -> dict:
    path = model_path(progress)
    try:
        model = _load_model(path)
    except Exception:
        # GPU 라이브러리 문제 시 CPU로 재시도
        from faster_whisper import WhisperModel

        model = WhisperModel(path, device="cpu", compute_type="int8")

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
