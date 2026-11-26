"""가사 받아쓰기(교차 검증)와 원문 가사 맞추기.

교차 검증: 보컬 분리본·원곡·섞은 소리를, 30초 창 위치를 바꿔 가며 여러 번 받아쓴 뒤
글자 단위 다수결(ensemble.py)로 확정한다. 의견이 갈린 구간은 잘라서 다시 듣고,
그래도 확신할 수 없는 글자는 sure=False 로 남겨 화면에 표시한다.

원문 가사: 사용자가 붙여 넣은 글자를 그대로 쓰고, 받아쓰기 결과는 시간 맞추기에만 쓴다.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from . import ensemble
from .config import WHISPER_MODEL, add_cuda_dll_dirs

HANGUL = re.compile(r"[가-힣]")
TOKEN = re.compile(r"[가-힣]|[A-Za-z0-9'’]+|[぀-ヿ一-鿿]")
SR = 16000
log = logging.getLogger("songscore")

# (소리, 앞에 붙이는 무음 초): 입력과 창 위치를 다르게 해서 서로 다른 실수를 하게 만든다
PASSES = [("voc", 0), ("mix", 0), ("voc", 10), ("mix", 18), ("blend", 22)]

# Whisper 가 소리가 없거나 악기 소리만 있을 때 자주 지어내는 문구 (실제 가사와 겹치지 않는 것만)
FAKE_PHRASES = ("다음 영상", "영상에서 만나", "시청해", "구독", "자막", "한효정", "국토정보공사", "아멘",
                "MBC 뉴스", "KBS", "SBS", "뉴스입니다", "Thank you for watching", "Thanks for watching",
                "Subscribe")

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


# ---------------------------------------------------------------- 모델

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


def _open_model(progress):
    path = model_path(progress)
    try:
        return _load_model(path)
    except Exception:
        # GPU 라이브러리 문제 시 CPU로 재시도
        from faster_whisper import WhisperModel

        return WhisperModel(path, device="cpu", compute_type="int8")


def is_fake(text: str) -> bool:
    low = text.lower()
    return any(p.lower() in low for p in FAKE_PHRASES)


def _words(model, audio, language: str | None, shift: float = 0.0, offset: float = 0.0, quick: bool = False):
    """한 번 받아쓰기 → (단어 목록, 감지 언어). 지어낸 문장은 통째로 버린다."""
    import numpy as np

    if shift:
        audio = np.concatenate([np.zeros(int(shift * SR), np.float32), audio])
    segments, info = model.transcribe(
        audio, language=language or None, word_timestamps=True,
        # 음성 구간 감지(VAD)는 노래를 말소리로 못 알아보고 통째로 잘라 내는 경우가 많아 끈다.
        vad_filter=False, beam_size=5, condition_on_previous_text=False,
        no_speech_threshold=0.8, hallucination_silence_threshold=2.0,
        # 잘라 낸 짧은 구간은 재시도 횟수를 줄인다 (안 들리는 구간에서 시간이 몇 배로 늘어남)
        temperature=[0.0, 0.4] if quick else [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    )
    out = []
    for seg in segments:
        ws = [w for w in (seg.words or []) if w.word.strip()]
        if is_fake(seg.text) or (seg.avg_logprob < -0.9 and len(ws) <= 3):
            continue
        for w in ws:
            s = float(w.start) - shift + offset
            if s < offset - 0.3:
                continue
            out.append({"s": round(max(0.0, s), 3), "e": round(max(0.0, float(w.end) - shift + offset), 3),
                        "w": w.word.strip(), "p": round(float(w.probability), 3)})
    return out, info.language


def _load_sources(vocals: Path, mix: Path) -> dict:
    import librosa

    voc, _ = librosa.load(str(vocals), sr=SR, mono=True)
    full, _ = librosa.load(str(mix), sr=SR, mono=True)
    n = min(len(voc), len(full))
    voc, full = voc[:n], full[:n]
    return {"voc": voc, "mix": full, "blend": (0.75 * voc + 0.25 * full).astype("float32")}


def _script(text: str) -> str | None:
    """가사 글자의 종류: 'hangul' / 'latin' / None(판단 어려움)."""
    h = len(HANGUL.findall(text))
    l = sum(1 for ch in text if ch.isascii() and ch.isalpha())
    if h + l == 0:
        return None
    return "hangul" if h >= l else "latin"


def _doubt_regions(units: list[dict], max_len: float = 20.0) -> list[tuple[float, float]]:
    """확신이 낮은 글자가 모인 구간."""
    regions: list[list[float]] = []
    for u in units:
        if u["agree"] >= 0.6 and u["prob"] >= 0.45:  # 의견이 크게 갈린 곳만 다시 듣는다
            continue
        a, b = u["t"], u["end"]
        if regions and a - regions[-1][1] < 3.0 and b - regions[-1][0] <= max_len:
            regions[-1][1] = max(regions[-1][1], b)
        else:
            regions.append([a, b])
    return [(a, b) for a, b in regions]


# ---------------------------------------------------------------- 교차 검증 받아쓰기

def transcribe_verified(vocals: Path, mix: Path, language: str | None = None,
                        progress=lambda p, msg="": None) -> dict:
    model = _open_model(progress)
    src = _load_sources(vocals, mix)
    total_s = len(src["voc"]) / SR

    if not language:  # 언어 자동 감지: 보컬만으로 한 번 들어 본다
        progress(0.03, "가사 언어 확인 중")
        first, language = _words(model, src["voc"], None)

    def run_all(lang):
        out = []
        for i, (name, shift) in enumerate(PASSES):
            progress(0.08 + 0.62 * i / len(PASSES), f"가사 듣기 {i + 1}/{len(PASSES)}")
            out.append(_words(model, src[name], lang, shift)[0])
        return out

    t0 = time.time()
    passes = run_all(language)
    units = ensemble.combine(passes)
    log.info("lyrics: main passes %.0fs", time.time() - t0)
    # 고른(감지한) 언어와 실제 글자가 다르면 맞는 언어로 다시
    script = _script(" ".join(u["text"] for u in units))
    fix = None
    if script == "latin" and language in ("ko", "ja", "zh"):
        fix = "en"
    elif script == "hangul" and language != "ko":
        fix = "ko"
    if fix:
        progress(0.08, "가사 언어를 맞춰 다시 듣는 중")
        passes2 = run_all(fix)
        units2 = ensemble.combine(passes2)
        if sum(u["sure"] for u in units2) >= sum(u["sure"] for u in units):
            passes, units, language = passes2, units2, fix

    # 의견이 갈린 구간은 잘라서 다시 듣기 (주 언어 + 다른 언어)
    other = "en" if language == "ko" else ("ko" if language == "en" else None)
    regions = _doubt_regions(units)
    for r, (a, b) in enumerate(regions):
        progress(0.72 + 0.26 * r / max(len(regions), 1), f"의심 구간 다시 듣기 {r + 1}/{len(regions)}")
        mid = (a + b) / 2
        ca = max(0.0, min(a - 1.5, mid - 5.0))
        cb = min(total_s, max(b + 1.5, mid + 5.0))
        i0, i1 = int(ca * SR), int(cb * SR)
        if i1 - i0 < SR:
            continue
        extra = [_words(model, src[name][i0:i1], language, 0.0, ca, quick=True)[0] for name in ("voc", "mix", "blend")]
        if other:
            alt = _words(model, src["voc"][i0:i1], other, 0.0, ca, quick=True)[0]
            # 다른 언어는 확실하게 들린 단어만 의견으로 인정 (섞인 영어·한글 가사 대비)
            want = "latin" if other == "en" else "hangul"
            alt = [w for w in alt if w["p"] >= 0.7 and _script(w["w"]) == want]
            if alt:
                extra.append(alt)
        lo, hi = a - 0.35, b + 0.35

        def inside(words):
            return [w for w in words if lo <= (w["s"] + w["e"]) / 2 <= hi]

        local = ensemble.combine([inside(p) for p in passes] + [inside(p) for p in extra])
        old = [u for u in units if lo <= u["t"] <= hi]
        # 다시 들은 결과가 더 확실할 때만 바꾼다
        # (글자 수가 줄어드는 교체는 하지 않는다: 들은 가사를 잃지 않도록)
        if local and len(local) >= len(old) and _certainty(local) > _certainty(old):
            units = sorted([u for u in units if not (lo <= u["t"] <= hi)] + local, key=lambda u: u["t"])
    log.info("lyrics: %d doubt regions done %.0fs", len(regions), time.time() - t0)
    # 목소리는 나는데 글자가 없는 곳은 그 부분만 잘라 다시 듣기
    holes = _loud_without_text(src["voc"], units)
    log.info("lyrics: %d holes %s", len(holes), holes)
    for a, b in holes:
        progress(0.97, "빠진 가사 다시 듣기")
        found = []
        for name, back in (("voc", 0.3), ("mix", 0.3), ("blend", 0.3), ("voc", 2.0), ("mix", 3.5)):
            ca = max(0.0, a - back)
            i0, i1 = int(ca * SR), int(min(total_s, b + 1.5) * SR)
            found.append(_words(model, src[name][i0:i1], language, 0.0, ca, quick=True)[0])
        local = ensemble.combine([[w for w in p if a - 0.5 <= (w["s"] + w["e"]) / 2 <= b + 0.5] for p in found])
        local = [u for u in local if not any(abs(u["t"] - o["t"]) < 0.4 for o in units)]
        # 두 번 이상 같게 들린 글자만 넣는다 (한 번만 들린 것은 지어낸 말일 수 있음)
        local = [u for u in local if u["agree"] >= 0.4]
        if len(local) >= 3:
            units = sorted(units + local, key=lambda u: u["t"])
    del model
    units = ensemble.dedupe(units)
    return {"language": language, "mode": "verified", "lines": lines_from_units(units), "units": units}


def _loud_without_text(voc, units: list[dict], step: float = 0.5, min_len: float = 3.0):
    """목소리는 크게 들리는데 받아쓴 글자가 없는 구간."""
    import numpy as np

    n = int(len(voc) / (SR * step))
    if n == 0:
        return []
    db = np.array([20 * np.log10(np.sqrt(np.mean(voc[int(i * step * SR):int((i + 1) * step * SR)] ** 2)) + 1e-9)
                   for i in range(n)])
    has = np.zeros(n, bool)
    for u in units:
        has[max(0, int((u["t"] - 0.6) / step)):min(n, int((u["end"] + 0.6) / step) + 1)] = True
    ref = np.median(db[has]) if has.any() else np.percentile(db, 75)
    loud = (db > ref - 9) & ~has
    runs: list[list[float]] = []
    for i in np.where(loud)[0]:
        t = i * step
        if runs and t - runs[-1][1] <= 1.0:
            runs[-1][1] = t + step
        else:
            runs.append([t, t + step])
    out = []
    for a, b in runs:
        while b - a >= min_len:
            out.append((float(a), float(min(b, a + 22.0))))
            a += 22.0
    return out


def _certainty(units: list[dict]) -> float:
    if not units:
        return 0.0
    return sum(u["agree"] * u["prob"] for u in units) / len(units)


def lines_from_units(units: list[dict], gap: float = 0.6, max_syl: int = 18) -> list[dict]:
    """글자 목록을 가사 줄로 묶는다 (쉬는 곳·길이 기준)."""
    words: list[list[dict]] = []
    for u in units:
        if words and not u.get("word_start", True) and u["t"] - words[-1][-1]["end"] < gap:
            words[-1].append(u)
        else:
            words.append([u])
    groups: list[list[list[dict]]] = []
    n_syl = 0
    for w in words:
        k = len(w)
        if groups and (w[0]["t"] - groups[-1][-1][-1]["end"] >= gap or n_syl + k > max_syl):
            groups.append([])
            n_syl = 0
        elif not groups:
            groups.append([])
        groups[-1].append(w)
        n_syl += k
    lines = []
    for g in groups:
        syl, texts = [], []
        for w in g:
            texts.append("".join(u["text"] for u in w))
            for u in w:
                parts = split_syllables(u["text"])
                if not parts:
                    continue
                step = max(u["end"] - u["t"], 0.05) / len(parts)
                for i, p in enumerate(parts):
                    syl.append({"t": round(u["t"] + i * step, 3), "text": p, "sure": bool(u.get("sure", True))})
        if syl:
            lines.append({"start": g[0][0]["t"], "end": g[-1][-1]["end"], "text": " ".join(texts), "syllables": syl})
    return lines


# ---------------------------------------------------------------- 원문 가사

def parse_original(text: str) -> list[str]:
    """붙여 넣은 원문에서 [Verse] 같은 표시와 빈 줄을 빼고 가사 줄만 남긴다."""
    out = []
    for raw in text.splitlines():
        line = re.sub(r"\[[^\]]*\]", " ", raw).strip()
        if line and TOKEN.search(line):
            out.append(line)
    return out


def _jamo(ch: str) -> tuple:
    if "가" <= ch <= "힣":
        c = ord(ch) - 0xAC00
        return (c // 588, (c % 588) // 28, c % 28)
    return ()


def _similar(a: str, b: str) -> float:
    if a == b:
        return 2.0
    x, y = _jamo(a), _jamo(b)
    if x and y:
        same = sum(p == q for p, q in zip(x, y))
        return 1.0 if same >= 2 else (0.2 if same == 1 else -0.6)
    if not x and not y:
        import difflib

        r = difflib.SequenceMatcher(a=a, b=b).ratio()
        return 2.0 * r - 0.4
    return -0.8


def align_original(text: str, heard: list[dict]) -> list[dict]:
    """원문 가사를 받아쓴 글자(시간 있음)에 맞춰 가사 줄을 만든다.

    들은 글자와 대응되는 곳은 그 시간(anchored), 대응이 없는 곳은 앞뒤 사이에 두고
    anchored=False 로 표시해 음표에 차례로 붙인다.
    """
    src_lines = parse_original(text)
    truth = []  # (줄 번호, 원래 토큰, 비교용)
    for li, line in enumerate(src_lines):
        for tok in ensemble.UNIT.findall(line):
            truth.append((li, tok, tok.lower().replace("’", "'")))
    if not truth or not heard:
        return []
    n, m = len(truth), len(heard)
    GAP = -0.7
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    back = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        score[i][0], back[i][0] = i * GAP, 1
    for j in range(1, m + 1):
        score[0][j], back[0][j] = j * GAP * 0.5, 2
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            best, how = score[i - 1][j - 1] + _similar(truth[i - 1][2], heard[j - 1]["key"]), 0
            if score[i - 1][j] + GAP > best:
                best, how = score[i - 1][j] + GAP, 1
            if score[i][j - 1] + GAP * 0.5 > best:
                best, how = score[i][j - 1] + GAP * 0.5, 2
            score[i][j], back[i][j] = best, how
    match: list[int | None] = [None] * n
    swapped: dict[int, int] = {}  # 글자는 다르게 들렸지만 같은 자리인 것 (시간만 참고)
    i, j = n, m
    while i and j:
        how = back[i][j]
        if how == 0:
            if _similar(truth[i - 1][2], heard[j - 1]["key"]) > 0.5:
                match[i - 1] = j - 1
            else:
                swapped[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif how == 1:
            i -= 1
        else:
            j -= 1
    # 혼자 동떨어진 대응(앞뒤 몇 글자 안에 가까운 시간의 대응이 없음)은 믿지 않는다
    anchors = [k for k in range(n) if match[k] is not None]
    good = []
    for a in anchors:
        t = heard[match[a]]["t"]
        if any(b != a and abs(b - a) <= 4 and abs(heard[match[b]]["t"] - t) <= 1.2 * abs(b - a) + 1.5
               for b in anchors):
            good.append(a)
    times: list[float | None] = [None] * n
    ends: list[float | None] = [None] * n
    anchored = [False] * n
    for k in good:
        times[k], ends[k], anchored[k] = heard[match[k]]["t"], heard[match[k]]["end"], True
    # 다르게 들린 글자도, 앞뒤 확실한 글자 사이의 시간이면 그 시각을 쓴다 ('확인 필요'로 남김)
    timed = list(anchored)
    for k, j in sorted(swapped.items()):
        t = heard[j]["t"]
        before = next((times[q] for q in range(k - 1, -1, -1) if times[q] is not None), None)
        after = next((times[q] for q in range(k + 1, n) if anchored[q]), None)
        lo = before if before is not None else t - 0.01
        hi = after if after is not None else t + 0.01
        if lo < t < hi and t - lo < 3.0 and hi - t < 3.0:
            times[k], ends[k], timed[k] = t, min(heard[j]["end"], hi), True
    k = 0
    while k < n:
        if times[k] is not None:
            k += 1
            continue
        e = k
        while e < n and times[e] is None:
            e += 1
        prev = ends[k - 1] if k else None
        nxt = times[e] if e < n else None
        count = e - k
        if prev is None:
            prev = max(0.0, (nxt if nxt is not None else heard[0]["t"] + 0.4 * count) - 0.4 * count)
        if nxt is None:
            nxt = prev + 0.4 * count
        step = max(nxt - prev, 0.05 * count) / count
        for q in range(count):
            times[k + q] = prev + step * q
            ends[k + q] = prev + step * (q + 0.9)
        k = e
    lines = []
    for li, line in enumerate(src_lines):
        idx = [k for k in range(n) if truth[k][0] == li]
        syl = []
        for k in idx:
            parts = split_syllables(truth[k][1])
            step = max(ends[k] - times[k], 0.05) / max(len(parts), 1)
            for q, p in enumerate(parts):
                syl.append({"t": round(times[k] + q * step, 3), "text": p, "sure": anchored[k],
                            "a": timed[k] and q == 0})
        if syl:
            lines.append({"start": round(times[idx[0]], 3), "end": round(ends[idx[-1]], 3), "text": line,
                          "syllables": syl})
    return lines


# ---------------------------------------------------------------- 점검용

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
