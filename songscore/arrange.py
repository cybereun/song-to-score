"""분석 결과 + 사용자 설정 → 악보 모델(마디·음표·코드·가사)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from . import harmony
from .lyrics import split_syllables

TIME_SIGS = {
    # 박자: (마디 길이(16분음표 단위), 한 박 길이, 코드 구간 길이, 박자 묶음)
    "4/4": (16, 4, 8, 8),
    "3/4": (12, 4, 12, 12),
    "2/4": (8, 4, 8, 8),
    "6/8": (12, 6, 6, 6),
}

KEY_NAMES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
FLAT_MAJOR = {5, 10, 3, 8, 1}  # F Bb Eb Ab Db
FLAT_MINOR = {2, 7, 0, 5, 10, 3}  # Dm Gm Cm Fm Bbm Ebm

MODES = {
    "lead": "멜로디 악보",
    "piano_easy": "피아노 악보 (쉬운 반주)",
    "piano_full": "피아노 악보 (반주 받아쓰기)",
    "guitar_melody": "기타 TAB (멜로디)",
    "guitar_chords": "기타 TAB (코드 반주)",
}


@dataclass
class Event:
    start: int  # 16분음표 단위, 악보 시작 기준
    length: int
    pitches: list[int]  # 빈 리스트 = 쉼표
    lyric: str = ""
    strings: list[int] | None = None  # 기타 줄 번호 (pitches 와 같은 순서)
    nid: int = -1  # 원래 멜로디 음표 번호 (화면 표시·가사 수정용)
    t0: float = 0.0  # 원곡에서의 시작·끝 시각(초)
    t1: float = 0.0


@dataclass
class Score:
    title: str
    artist: str
    mode: str
    time_sig: str
    bar_units: int
    n_bars: int
    bpm: int
    key_tonic: int
    key_mode: str
    flats: bool
    melody: list[Event]
    chords: list[tuple[int, int, tuple[int, str] | None]]  # (start, length, (root, quality))
    lower: list[Event] = field(default_factory=list)  # 피아노 왼손 / 기타 반주
    lyrics_lines: list[str] = field(default_factory=list)
    show_lyrics: bool = True
    lyrics_page: bool = True
    bars_per_line: int = 4

    @property
    def key_label(self) -> str:
        name = KEY_NAMES[self.key_tonic]
        if self.flats and "#" in name:
            name = {"C#": "Db", "F#": "Gb"}[name]
        if not self.flats and name in ("Db", "Gb"):
            name = {"Db": "C#", "Gb": "F#"}[name]
        return f"{name} {'major' if self.key_mode == 'major' else 'minor'}"


# ---------------------------------------------------------------- 박 격자

def _beat_grid(beat_times: list[float], mult: float) -> np.ndarray:
    bt = np.asarray(beat_times, float)
    if mult == 2:
        mids = (bt[:-1] + bt[1:]) / 2
        out = np.empty(len(bt) + len(mids))
        out[0::2] = bt
        out[1::2] = mids
        return out
    if mult == 0.5:
        return bt[0::2]
    return bt


class TimeMap:
    """초 → 박 위치(실수). 박 사이를 선형 보간하고 바깥은 연장."""

    def __init__(self, beats: np.ndarray):
        self.beats = beats
        self.idx = np.arange(len(beats), dtype=float)
        self.period = float(np.median(np.diff(beats))) if len(beats) > 1 else 0.5

    def __call__(self, t: float) -> float:
        b = self.beats
        if t < b[0]:
            return (t - b[0]) / self.period
        if t > b[-1]:
            return self.idx[-1] + (t - b[-1]) / self.period
        return float(np.interp(t, b, self.idx))

    def time_of(self, beat: float) -> float:
        b = self.beats
        if beat < 0:
            return b[0] + beat * self.period
        if beat > len(b) - 1:
            return b[-1] + (beat - (len(b) - 1)) * self.period
        return float(np.interp(beat, self.idx, b))


def _auto_downbeat(an: dict, beats: np.ndarray, beats_per_bar: int) -> int:
    if beats_per_bar <= 1:
        return 0
    orig = np.asarray(an["beat_times"], float)
    onset = np.asarray(an["beat_onset"], float)
    bass = np.asarray(an["beat_bass"], float)
    # 새 격자의 각 박에 가장 가까운 원래 박 특징을 사용
    near = np.clip(np.searchsorted(orig, beats), 0, len(orig) - 1)
    o = onset[near]
    bchg = np.zeros(len(beats))
    bb = bass[near]
    bchg[1:] = np.linalg.norm(np.diff(bb, axis=0), axis=1)
    o = o / (o.max() + 1e-9)
    bchg = bchg / (bchg.max() + 1e-9)
    score = o + bchg
    best, best_p = -1, 0
    for p in range(beats_per_bar):
        s = float(np.mean(score[p::beats_per_bar]))
        if s > best:
            best, best_p = s, p
    return best_p


# ---------------------------------------------------------------- 가사

def _lyric_syllables(an: dict, lyrics_text: str | None) -> tuple[list[tuple[float, str]], list[str]]:
    lines = an.get("lyrics", {}).get("lines", [])
    if lyrics_text is None:
        sy = [(s["t"], s["text"], True) for ln in lines for s in ln["syllables"]]
        return sy, [ln["text"] for ln in lines]
    edited = [l.strip() for l in lyrics_text.splitlines()]
    edited = [l for l in edited if l]
    return align_edited(lines, edited), edited


def align_edited(lines: list[dict], edited: list[str]) -> list[tuple[float, str, bool]]:
    """고친 가사를 줄 번호가 아니라 '음절 순서'로 원래 인식 결과에 맞춘다.

    줄을 합치거나 나누거나, 글자를 넣고 빼도 같은 글자는 원래 시간을 그대로 쓰고,
    새로 넣은 글자는 앞뒤 글자 시간 사이에 고르게 배치한다.
    """
    import difflib

    old = [(s["t"], s["text"]) for ln in lines for s in ln["syllables"]]
    new = [s for text in edited for s in split_syllables(text)]
    if not old or not new:
        return []
    times: list[float | None] = [None] * len(new)
    anchored = [False] * len(new)
    sm = difflib.SequenceMatcher(a=[o[1] for o in old], b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or (tag == "replace" and i2 - i1 == j2 - j1):
            for k in range(j2 - j1):
                times[j1 + k] = old[i1 + k][0]
                anchored[j1 + k] = True
        elif tag == "replace":  # 글자 수가 다르게 바뀜 → 원래 구간에 고르게
            t0 = old[i1][0]
            t1 = old[i2][0] if i2 < len(old) else old[i2 - 1][0] + 0.4
            step = (t1 - t0) / (j2 - j1)
            for k in range(j2 - j1):
                times[j1 + k] = t0 + k * step
    # 새로 넣은 글자: 앞뒤 시간 사이를 나눠 채움
    k = 0
    while k < len(new):
        if times[k] is not None:
            k += 1
            continue
        e = k
        while e < len(new) and times[e] is None:
            e += 1
        prev = times[k - 1] if k > 0 else None
        nxt = times[e] if e < len(new) else None
        if prev is None and nxt is None:
            prev, nxt = old[0][0], old[-1][0]
        elif prev is None:
            prev = nxt - 0.35 * (e - k)
        elif nxt is None:
            nxt = prev + 0.35 * (e - k + 1)
        step = (nxt - prev) / (e - k + 1)
        for m in range(k, e):
            times[m] = prev + step * (m - k + 1)
        k = e
    return [(float(t), s, a) for t, s, a in zip(times, new, anchored)]


def _fill_missing_notes(mel: list[dict], syl: list[tuple[float, str]]) -> list[dict]:
    """가사 음절이 있는데 근처에 음표가 없으면(주로 줄 첫 글자의 약한 자음)
    바로 뒤 음의 높이로 짧은 음표를 만들어 채운다."""
    if not mel:
        return mel
    starts = np.array([n["start"] for n in mel])
    ends = np.array([n["end"] for n in mel])
    extra = []
    for t, _, anchored in syl:
        if not anchored or np.any((starts <= t + 0.3) & (ends >= t - 0.05)):
            continue
        nxt = np.where(starts > t)[0]
        prv = np.where(ends <= t)[0]
        ref = mel[int(nxt[0])] if len(nxt) else mel[int(prv[-1])]
        end = min(float(starts[nxt[0]]) if len(nxt) else t + 0.4, t + 0.5)
        if end - t < 0.08:
            continue
        extra.append({"start": round(t, 3), "end": round(end, 3), "midi": ref["midi"],
                      "conf": 0.0, "nid": 100000 + int(round(t * 100))})
    return sorted(mel + extra, key=lambda n: n["start"]) if extra else mel


def _assign_lyrics(notes: list[dict], syl: list[tuple[float, str, bool]]) -> list[str]:
    """음절을 음표에 하나씩 순서대로 배정한다 (한 음표에 여러 글자가 몰리지 않게)."""
    starts = np.array([n["start"] for n in notes]) if notes else np.zeros(0)
    res = [""] * len(notes)
    if not len(starts):
        return res
    last, last_t = -1, -1e9
    for idx, (t, text, anchored) in enumerate(syl):
        nxt_t = syl[idx + 1][0] if idx + 1 < len(syl) else t + 1.0
        if not anchored:
            # 인식 결과에 없던(직접 넣은) 글자: 바로 다음 빈 음표에 순서대로 붙인다.
            # 단, 다음에 오는 '시간이 확실한' 글자의 자리는 넘지 않는다.
            limit = next((s2[0] for s2 in syl[idx + 1:] if s2[2]), float("inf"))
            j = last + 1
            while j < len(starts) and starts[j] < last_t - 0.01:
                j += 1
            if j < len(starts) and starts[j] < limit - 0.1:
                res[j] = text
                last, last_t = j, starts[j]
                continue
            if last >= 0:
                sep = "" if not res[last][-1:].isascii() and not text[0].isascii() else " "
                res[last] = (res[last] + sep + text) if res[last] else text
            continue
        # 아직 안 쓴 음표 가운데 음절 시작 근처에서 시작하는 것
        cand = [j for j in range(last + 1, len(starts))
                if t - 0.25 <= starts[j] <= max(t + 0.4, min(nxt_t - 0.02, t + 0.8))]
        if cand:
            j = min(cand, key=lambda j: abs(starts[j] - (t + 0.03)))
        else:
            # 빈 음표가 없으면 바로 앞 음표에 붙임 (가까울 때만)
            j = last if last >= 0 and abs(starts[last] - t) < 0.8 else -1
            if j < 0:
                near = int(np.argmin(np.abs(starts - t)))
                if abs(starts[near] - t) > 0.8:
                    continue
                j = near
        if res[j]:
            sep = "" if not res[j][-1].isascii() and not text[0].isascii() else " "
            res[j] = res[j] + sep + text
        else:
            res[j] = text
        if j > last:
            last, last_t = j, starts[j]
    return res


# ---------------------------------------------------------------- 기타 코드 모양

GUITAR_TUNING = [64, 59, 55, 50, 45, 40]  # 1번 줄 ~ 6번 줄
CHORD_IVS = {q: ivs for q, (ivs, _) in harmony.CHORD_TYPES.items()}

OPEN_SHAPES = {  # (root, quality): 6번줄→1번줄 프렛, None = 치지 않음
    (0, ""): [None, 3, 2, 0, 1, 0], (2, ""): [None, None, 0, 2, 3, 2],
    (4, ""): [0, 2, 2, 1, 0, 0], (7, ""): [3, 2, 0, 0, 0, 3], (9, ""): [None, 0, 2, 2, 2, 0],
    (9, "m"): [None, 0, 2, 2, 1, 0], (2, "m"): [None, None, 0, 2, 3, 1], (4, "m"): [0, 2, 2, 0, 0, 0],
    (4, "7"): [0, 2, 0, 1, 0, 0], (9, "7"): [None, 0, 2, 0, 2, 0], (2, "7"): [None, None, 0, 2, 1, 2],
    (7, "7"): [3, 2, 0, 0, 0, 1], (0, "7"): [None, 3, 2, 3, 1, 0], (11, "7"): [None, 2, 1, 2, 0, 2],
    (9, "m7"): [None, 0, 2, 0, 1, 0], (4, "m7"): [0, 2, 0, 0, 0, 0], (2, "m7"): [None, None, 0, 2, 1, 1],
    (0, "maj7"): [None, 3, 2, 0, 0, 0], (5, "maj7"): [None, None, 3, 2, 1, 0],
    (2, "sus4"): [None, None, 0, 2, 3, 3], (9, "sus4"): [None, 0, 2, 2, 3, 0], (4, "sus4"): [0, 2, 2, 2, 0, 0],
}
E_SHAPE = {"": [0, 2, 2, 1, 0, 0], "m": [0, 2, 2, 0, 0, 0], "7": [0, 2, 0, 1, 0, 0],
           "m7": [0, 2, 0, 0, 0, 0], "maj7": [0, None, 1, 1, 0, None], "sus4": [0, 2, 2, 2, 0, 0],
           "dim": [0, 1, 2, 0, 2, None]}
A_SHAPE = {"": [None, 0, 2, 2, 2, 0], "m": [None, 0, 2, 2, 1, 0], "7": [None, 0, 2, 0, 2, 0],
           "m7": [None, 0, 2, 0, 1, 0], "maj7": [None, 0, 2, 1, 2, 0], "sus4": [None, 0, 2, 2, 3, 0],
           "dim": [None, 0, 1, 2, 1, None]}


def guitar_shape(root: int, q: str) -> list[int | None]:
    if (root, q) in OPEN_SHAPES:
        return OPEN_SHAPES[(root, q)]
    fe = (root - 4) % 12  # 6번줄 E 기준 프렛
    fa = (root - 9) % 12  # 5번줄 A 기준 프렛
    fe = fe if fe > 0 else 12
    fa = fa if fa > 0 else 12
    if fe <= fa:
        return [None if f is None else f + fe for f in E_SHAPE[q]]
    return [None if f is None else f + fa for f in A_SHAPE[q]]


def shape_notes(shape: list[int | None]) -> tuple[list[int], list[int]]:
    pitches, strings = [], []
    for k, fret in enumerate(shape):  # k=0 → 6번줄
        if fret is None:
            continue
        s = 6 - k
        pitches.append(GUITAR_TUNING[s - 1] + fret)
        strings.append(s)
    return pitches, strings


# ---------------------------------------------------------------- 메인

def build_score(an: dict, st: dict) -> Score:
    ts = st.get("time_sig", "4/4")
    bar_units, upb, chord_units, _group = TIME_SIGS.get(ts, TIME_SIGS["4/4"])
    beats_per_bar = bar_units // upb
    grid = 2 if int(st.get("grid", 16)) == 8 else 1
    mult = float(st.get("tempo_mult", 1))
    transpose = int(st.get("transpose", 0))
    mode = st.get("mode", "lead")

    beats = _beat_grid(an["beat_times"], mult)
    tm = TimeMap(beats)
    phase = _auto_downbeat(an, beats, beats_per_bar) + int(st.get("downbeat_shift", 0))

    def units(t: float) -> float:
        return (tm(t) - phase) * upb

    # ---- 멜로디 + 가사 (시간 영역)
    mel = sorted(an.get("melody", []), key=lambda n: n["start"])
    for i, n in enumerate(mel):
        n.setdefault("nid", i)
    syl, lyric_lines = _lyric_syllables(an, st.get("lyrics_text"))
    mel = _fill_missing_notes(mel, syl)
    lyr = _assign_lyrics(mel, syl)
    # 음표별로 직접 고친 가사 (화면에서 음표를 눌러 수정한 것)
    edits = {str(k): str(v).strip() for k, v in (st.get("lyric_edits") or {}).items()}
    for i, n in enumerate(mel):
        if str(n["nid"]) in edits:
            lyr[i] = edits[str(n["nid"])]

    raw = []
    for n, ly in zip(mel, lyr):
        s = round(units(n["start"]) / grid) * grid
        e = round(units(n["end"]) / grid) * grid
        if e <= s:
            e = s + grid
        raw.append([s, e, n["midi"] + transpose, ly, n["end"] - n["start"], n["nid"], n["start"], n["end"]])
    # 겹침 정리 (단선율)
    raw.sort(key=lambda r: (r[0], -r[4]))
    mono: list[list] = []
    for r in raw:
        if mono and r[0] == mono[-1][0]:
            if r[3] and mono[-1][3] and r[1] > r[0] + grid:
                r[0] += grid  # 둘 다 가사가 있으면 뒤 음표를 한 칸 밀어 둘 다 살림
            else:
                if r[3] and not mono[-1][3]:
                    mono[-1][3] = r[3]
                continue
        if mono and r[0] < mono[-1][1]:
            mono[-1][1] = r[0]
        mono.append(r)
    # 한 칸짜리 틈 메우기
    for a, b in zip(mono, mono[1:]):
        gap = b[0] - a[1]
        if 0 < gap <= grid and a[1] - a[0] >= 2 * grid:
            a[1] = b[0]
    # 같은 음이 가사 없이 바로 이어지면 하나로 합치기
    merged: list[list] = []
    for r in mono:
        if merged and r[2] == merged[-1][2] and r[0] == merged[-1][1] and not r[3]:
            merged[-1][1] = r[1]
            merged[-1][7] = r[7]
            continue
        merged.append(r)
    # 앞뒤로 동떨어진 16분음표 하나짜리 잡음 제거
    mono = []
    for i, r in enumerate(merged):
        if r[1] - r[0] <= 1 and not r[3]:
            prev_gap = r[0] - merged[i - 1][1] if i else 99
            next_gap = merged[i + 1][0] - r[1] if i + 1 < len(merged) else 99
            if prev_gap >= 4 and next_gap >= 4:
                continue
        mono.append(r)

    # ---- 악보 시작/끝 마디
    energy = np.asarray(an.get("beat_energy", []), float)
    orig_bt = np.asarray(an["beat_times"], float)
    active_t = orig_bt[energy > 0.12 * energy.max()] if len(energy) and energy.max() > 0 else orig_bt
    first_u = min([m[0] for m in mono] + ([units(active_t[0])] if len(active_t) else []))
    last_u = max([m[1] for m in mono] + ([units(active_t[-1]) + upb] if len(active_t) else []))
    bar0 = math.floor(first_u / bar_units)
    n_bars = max(1, math.ceil(last_u / bar_units) - bar0)
    off = bar0 * bar_units
    total = n_bars * bar_units

    melody = [Event(m[0] - off, m[1] - m[0], [m[2]], m[3], nid=m[5], t0=m[6], t1=m[7]) for m in mono
              if 0 <= m[0] - off < total]
    for ev in melody:
        ev.length = min(ev.length, total - ev.start)

    # ---- 조성
    pcs = np.zeros(12)
    for m in mono:
        pcs[(m[2] - transpose) % 12] += m[1] - m[0]
    if st.get("key") and st["key"] != "auto":
        name, kmode = st["key"].split()
        key = {"tonic": (KEY_NAMES.index(name) - transpose) % 12, "mode": kmode}
    else:
        key = an.get("key") or harmony.estimate_key(np.asarray(an["global_chroma"]), pcs)
    key_t = {"tonic": (key["tonic"] + transpose) % 12, "mode": key["mode"]}

    # ---- 코드 (구간 시간 범위로 원래 박 특징 평균)
    bc = np.asarray(an["beat_chroma"], float)
    bb = np.asarray(an["beat_bass"], float)
    be = np.asarray(an["beat_energy"], float)
    n_seg = total // chord_units
    seg_c, seg_b, seg_e = [], [], []
    for i in range(n_seg):
        u0 = off + i * chord_units
        t0 = tm.time_of(u0 / upb + phase)
        t1 = tm.time_of((u0 + chord_units) / upb + phase)
        idx = np.where((orig_bt >= t0 - 0.02) & (orig_bt < t1 - 0.02))[0]
        if not len(idx):
            idx = [int(np.argmin(np.abs(orig_bt - t0)))]
        seg_c.append(bc[idx].mean(0))
        seg_b.append(bb[idx].mean(0))
        seg_e.append(be[idx].mean())
    est = harmony.estimate_chords(np.array(seg_c), np.array(seg_b), np.array(seg_e), key,
                                  change_penalty=float(st.get("chord_stability", 0.12)))
    chords: list = []
    for i, ch in enumerate(est):
        if ch is not None:
            ch = ((ch[0] + transpose) % 12, ch[1])
        if chords and chords[-1][2] == ch:
            chords[-1][1] += chord_units
        else:
            chords.append([i * chord_units, chord_units, ch])
    chords = [tuple(c) for c in chords]

    flats = key_t["tonic"] in (FLAT_MAJOR if key_t["mode"] == "major" else FLAT_MINOR)
    bpm = int(round(60.0 / tm.period)) if tm.period > 0 else 100
    if ts == "6/8":
        bpm = bpm  # 점4분음표 기준

    sc = Score(
        title=st.get("title") or "제목 없음", artist=st.get("artist", ""), mode=mode,
        time_sig=ts, bar_units=bar_units, n_bars=n_bars, bpm=bpm,
        key_tonic=key_t["tonic"], key_mode=key_t["mode"], flats=flats,
        melody=melody, chords=chords, lyrics_lines=lyric_lines,
        show_lyrics=bool(st.get("show_lyrics", True)), lyrics_page=bool(st.get("lyrics_page", True)),
        bars_per_line=int(st.get("bars_per_line", 4)),
    )

    # ---- 모드별 아래 성부
    if mode == "piano_easy":
        sc.lower = _piano_easy(chords, upb)
    elif mode == "piano_full":
        acc = an.get("accomp_notes") or []
        sc.lower = _piano_full(acc, units, off, total, transpose) if acc else _piano_easy(chords, upb)
    elif mode == "guitar_chords":
        sc.lower = _guitar_strum(chords, upb, ts)
    if mode.startswith("guitar"):
        for ev in sc.melody:
            p = ev.pitches[0] - 12
            while p < 40:
                p += 12
            while p > 86:
                p -= 12
            ev.pitches = [p]
    return sc


def _bass_pitch(root: int) -> int:
    p = 36 + root  # C2..B2
    return p + 12 if p < 40 else p  # E2 ~ D#3


def _triad(root: int, q: str) -> list[int]:
    ivs = CHORD_IVS[q]
    base = 48 + root
    if base > 55:
        base -= 12
    notes = sorted({base + iv for iv in ivs})
    return [n if n < 62 else n - 12 for n in notes]


def _piano_easy(chords, upb) -> list[Event]:
    out = []
    for start, length, ch in chords:
        if ch is None:
            continue
        root, q = ch
        bass = _bass_pitch(root)
        tri = sorted(set(_triad(root, q)))
        pos = start
        end = start + length
        while pos < end:
            seg = min(8 if upb == 4 else 6, end - pos)
            if seg > upb:
                out.append(Event(pos, upb, [bass]))
                out.append(Event(pos + upb, seg - upb, tri))
            else:
                out.append(Event(pos, seg, [bass] + tri))
            pos += seg
    return out


def _piano_full(acc, units, off, total, transpose) -> list[Event]:
    g = 2
    groups: dict[int, list] = {}
    for n in acc:
        p = n["midi"] + transpose
        if not (28 <= p < 62) or n.get("amp", 1) < 0.25:
            continue
        s = round(units(n["start"]) / g) * g - off
        e = round(units(n["end"]) / g) * g - off
        if e <= s:
            e = s + g
        if 0 <= s < total:
            groups.setdefault(s, []).append((p, e))
    starts = sorted(groups)
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else total
        end = min(max(e for _, e in groups[s]), nxt, total)
        ps = sorted({p for p, _ in groups[s]})
        if len(ps) > 4:
            ps = [ps[0]] + ps[-3:]
        out.append(Event(s, max(end - s, 1), ps))
    return out


def _guitar_strum(chords, upb, ts) -> list[Event]:
    out = []
    for start, length, ch in chords:
        if ch is None:
            continue
        pitches, strings = shape_notes(guitar_shape(*ch))
        pos = start
        while pos < start + length:
            d = min(upb, start + length - pos)
            out.append(Event(pos, d, list(pitches), strings=list(strings)))
            pos += d
    return out
