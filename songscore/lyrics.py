"""faster-whisper로 가사 받아쓰기 + 음절 단위 시간 정보."""
from __future__ import annotations

import re
from pathlib import Path

from .config import WHISPER_MODEL, add_cuda_dll_dirs

HANGUL = re.compile(r"[가-힣]")
TOKEN = re.compile(r"[가-힣]|[A-Za-z0-9'’]+|[぀-ヿ一-鿿]")


_HYPH = None


def _hyphenate(word: str) -> list[str]:
    """영어 단어를 노래 음절로 나눈다 (Some-where, rain-bow). 나눈 앞 조각 끝에 '-' 를 붙인다."""
    global _HYPH
    if len(word) < 4 or not word.isascii() or not word.replace("'", "").replace("’", "").isalpha():
        return [word]
    try:
        if _HYPH is None:
            import pyphen

            _HYPH = pyphen.Pyphen(lang="en_US", left=1, right=2)
        parts = _HYPH.inserted(word).split("-")
    except Exception:
        return [word]
    parts = [p for p in parts if p]
    return [p + "-" for p in parts[:-1]] + parts[-1:] if len(parts) > 1 else [word]


def split_syllables(text: str) -> list[str]:
    """한글·한자·가나는 한 글자씩, 영어는 음절 단위(앞 조각은 '-'로 끝남)로 나눈다."""
    out: list[str] = []
    for tok in TOKEN.findall(text):
        out += _hyphenate(tok) if tok[:1].isascii() else [tok]
    return out


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
    lines, lang = _run(model, audio, language, 0.0, progress)
    # 고른 언어와 실제 가사 언어가 다르면(예: '한국어'로 골랐는데 영어 곡) 맞는 언어로 한 번 더
    script, share = main_script(lines)
    fix = None
    if share >= 0.7 and language:
        if script == "latin" and language in ("ko", "ja", "zh"):
            fix = "en"
        elif script == "hangul" and language != "ko":
            fix = "ko"
    if fix:
        progress(0.6, "가사 언어를 맞춰 다시 받아쓰는 중")
        lines2, lang2 = _run(model, audio, fix, 0.0)
        if sum(len(l["syllables"]) for l in lines2) >= sum(len(l["syllables"]) for l in lines):
            lines, lang = lines2, lang2
    del model
    return {"language": lang, "lines": lines}


def _run(model, audio, language: str | None, offset: float = 0.0, progress=None,
         skip: list[tuple[float, float]] | None = None) -> tuple[list[dict], str]:
    """오디오(16kHz)를 받아써서 가사 줄 목록을 돌려준다. offset 초만큼 시간을 밀어 준다.
    skip 구간 안에 들어가는 단어(이미 있는 가사)는 뺀다."""
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
                ws, we = float(w.start) + offset, float(w.end) + offset
                mid = (ws + we) / 2
                if skip and any(a <= mid <= b for a, b in skip):
                    continue
                words.append({"start": ws, "end": we, "text": text})
        for ln in group_lines(words):
            ln["lp"] = round(float(seg.avg_logprob), 3)  # 받아쓰기 신뢰도 (헛인식 판단용)
            ln["cr"] = round(float(seg.compression_ratio), 2)
            lines.append(ln)
        if progress:
            progress(min(0.99, seg.end / total), "가사 인식 중")
    return lines, info.language


def sung_without_lyrics(lines: list[dict], notes: list[dict], step: float = 0.5) -> list[tuple[float, float]]:
    """노래(멜로디 음표)는 있는데 가사 줄이 하나도 걸치지 않는 구간을 0.5초 단위로 찾는다."""
    if not notes:
        return []
    end = max(n["end"] for n in notes)
    nb = int(end / step) + 2
    sung = [0.0] * nb
    for n in notes:
        a, b = n["start"], n["end"]
        i = int(a / step)
        while i * step < b and i < nb:
            sung[i] += max(0.0, min(b, (i + 1) * step) - max(a, i * step))
            i += 1
    lyric = [False] * nb
    for ln in lines:
        # 가사 줄 앞뒤로 약간 여유
        for i in range(max(0, int((ln["start"] - 0.4) / step)), min(nb, int((ln["end"] + 0.4) / step) + 1)):
            lyric[i] = True
    runs: list[list[float]] = []
    for i in range(nb):
        hole = sung[i] >= 0.3 * step and not lyric[i]
        if hole:
            t = i * step
            if runs and t - runs[-1][1] <= 1.0:  # 1초 이하로 끊긴 건 한 구간
                runs[-1][1] = t + step
            else:
                runs.append([t, t + step])
    return [(a, b) for a, b in runs]


def missing_regions(lines: list[dict], notes: list[dict], max_len: float = 25.0) -> list[tuple[float, float]]:
    """다시 받아쓸 구간 목록: 가사 없이 노래만 2초 이상 이어지는 곳 (앞뒤 여유 포함)."""
    holes = [(a, b) for a, b in sung_without_lyrics(lines, notes) if b - a >= 2.0]
    merged: list[list[float]] = []
    for a, b in holes:
        if merged and a - merged[-1][1] < 3.0 and b - merged[-1][0] <= max_len:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        # 너무 긴 구간은 나눠서 (Whisper 한 번에 30초까지)
        while b - a > max_len:
            out.append((max(0.0, a - 0.8), a + max_len))
            a += max_len - 2.0
        out.append((max(0.0, a - 0.8), b + 0.8))
    return out


def fill_missing(vocals: Path, result: dict, notes: list[dict], language: str | None = None,
                 progress=lambda p, msg="": None) -> dict:
    """긴 곡을 한 번에 받아쓸 때 Whisper 가 첫 부분 등을 통째로 건너뛰는 경우가 있다.
    노래는 있는데 가사가 비어 있는 구간만 잘라서 다시 받아쓴 뒤 끼워 넣는다."""
    regions = missing_regions(result["lines"], notes)
    if not regions:
        return result
    import librosa

    progress(0.5, f"빠진 가사 다시 확인 중 ({len(regions)}곳)")
    path = model_path()
    try:
        model = _load_model(path)
    except Exception:
        from faster_whisper import WhisperModel

        model = WhisperModel(path, device="cpu", compute_type="int8")
    audio, sr = librosa.load(str(vocals), sr=16000, mono=True)
    lang = language or result.get("language")
    # 곡의 주 언어로 다시 받아쓴다 (예: 언어를 '한국어'로 골랐어도 영어 곡이면 영어로)
    script, share = main_script(result["lines"])
    if share >= 0.7:
        if script == "latin" and lang in (None, "ko", "ja", "zh"):
            lang = "en"
        elif script == "hangul":
            lang = "ko"
    lines = list(result["lines"])
    total_added = 0
    for _pass in range(2):  # 다시 받아써도 빈 곳이 남으면 한 번 더
        added = []
        for a, b in regions:
            clip = audio[int(a * sr): int(b * sr)]
            if len(clip) < sr:
                continue
            # 이미 있는 가사와 겹치는 단어는 빼고, 빈 구간의 단어만 쓴다
            skip = [(o["start"] - 0.45, o["end"] + 0.45) for o in lines + added]
            new, _ = _run(model, clip, lang, offset=a, skip=skip)
            for ln in new:
                if ln["end"] < a or ln["start"] > b:
                    continue
                if _looks_fake(ln, notes, script if share >= 0.7 else None):
                    continue
                # Whisper 가 같은 구간을 두 번 내놓는 경우: 이미 넣은 줄과 시간이 겹치면 버림
                dur = max(ln["end"] - ln["start"], 0.1)
                if any(min(ln["end"], o["end"]) - max(ln["start"], o["start"]) > 0.3 * dur for o in lines + added):
                    continue
                added.append(ln)
        if not added:
            break
        lines = sorted(lines + added, key=lambda ln: ln["start"])
        total_added += len(added)
        regions = missing_regions(lines, notes)
        if not regions:
            break
    del model
    if not total_added:
        return result
    return {**result, "lines": lines, "refilled": total_added}


# Whisper 가 소리가 없거나 악기 소리만 있을 때 자주 지어내는 문구 (실제 가사와 겹치지 않는 것만)
FAKE_PHRASES = ("다음 영상", "시청해", "구독", "자막", "국토정보공사", "아멘", "MBC", "KBS", "SBS", "뉴스입니다",
                "Thank you for watching", "Thanks for watching", "Subscribe")


def _script(text: str) -> str | None:
    """가사 글자의 종류: 'hangul' / 'latin' / None(판단 어려움)."""
    h = len(HANGUL.findall(text))
    l = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if h + l == 0:
        return None
    return "hangul" if h >= l else "latin"


def _looks_fake(ln: dict, notes: list[dict], script: str | None) -> bool:
    """다시 받아쓴 줄이 헛인식(지어낸 문구)인지.

    실제 노래 가사: 받아쓰기 신뢰도(avg_logprob) 약 -0.3~-0.5, 음표 수와 글자 수가 비슷함.
    지어낸 문구("Thank you.", "아멘" 등): 신뢰도 -0.7 이하, 음표는 많은데 글자는 2~3개.
    """
    text = ln["text"].lower()
    if any(p.lower() in text for p in FAKE_PHRASES):
        return True
    if script and _script(ln["text"]) not in (None, script):  # 곡 언어와 다른 글자 (영어 곡의 한글 등)
        return True
    if ln.get("lp", 0.0) < -0.7:
        return True
    n_syl = len(ln["syllables"])
    n_notes = sum(1 for n in notes if n["end"] > ln["start"] - 0.5 and n["start"] < ln["end"] + 1.5)
    return n_syl <= 3 and n_notes >= 4 * n_syl


def main_script(lines: list[dict]) -> tuple[str | None, float]:
    """곡 전체에서 가장 많이 쓰인 글자 종류와 그 비율."""
    count = {"hangul": 0, "latin": 0}
    for ln in lines:
        for s in ln["syllables"]:
            sc = _script(s["text"])
            if sc:
                count[sc] += 1
    tot = sum(count.values())
    if not tot:
        return None, 0.0
    best = max(count, key=count.get)
    return best, count[best] / tot


def drop_unsung(result: dict, notes: list[dict]) -> dict:
    """멜로디 음표가 거의 없는 구간의 문장(무음에서 생긴 헛인식)을 버린다."""
    keep = []
    for ln in result["lines"]:
        inside = sum(1 for n in notes if n["end"] > ln["start"] and n["start"] < ln["end"])
        fake = any(p.lower() in ln["text"].lower() for p in FAKE_PHRASES)
        n_syl = len(ln["syllables"])
        # 전주·간주에서 생긴 짧은 헛인식: 음표는 많은데 글자는 2~3개이고 신뢰도가 낮음
        thin = n_syl <= 3 and ln.get("lp", 0.0) < -0.6 and \
            sum(1 for n in notes if n["end"] > ln["start"] - 0.5 and n["start"] < ln["end"] + 1.5) >= 4 * n_syl
        if inside >= max(1, n_syl // 4) and not fake and not thin:
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
