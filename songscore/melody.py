"""분리된 보컬에서 멜로디 음표 추출 (torchcrepe 음높이 추적 + 음표 분할)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

HOP_S = 0.01  # 10ms


def track_pitch(vocals: Path, progress=lambda p, msg="": None) -> dict:
    import librosa
    import torch
    import torchcrepe

    sr = 16000
    y, _ = librosa.load(str(vocals), sr=sr, mono=True)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    hop = int(sr * HOP_S)
    audio = torch.from_numpy(y)[None]
    progress(0.1, "멜로디 음높이 추적 중")
    pitch, period = torchcrepe.predict(
        audio, sr, hop, fmin=65.0, fmax=1050.0, model="full",
        decoder=torchcrepe.decode.viterbi, return_periodicity=True,
        batch_size=1024, device=device,
    )
    pitch = pitch[0].cpu().numpy()
    period = period[0].cpu().numpy()
    period = torchcrepe.filter.median(torch.from_numpy(period)[None], 3)[0].numpy()

    rms = librosa.feature.rms(y=y, frame_length=1024, hop_length=hop, center=True)[0]
    n = min(len(pitch), len(rms))
    pitch, period, rms = pitch[:n], period[:n], rms[:n]
    db = 20 * np.log10(rms + 1e-9)
    db -= db.max()
    if device == "cuda":
        torch.cuda.empty_cache()
    progress(1.0, "멜로디 음높이 추적 완료")
    return {"f0": pitch, "conf": period, "db": db}


def _median(a):
    return float(np.median(a)) if len(a) else 0.0


def segment_notes(track: dict, split_times: list[float] | None = None,
                  conf_th: float = 0.45, db_th: float = -38.0) -> list[dict]:
    """음높이 곡선을 음표로 나눈다. split_times(가사 음절 시작 시각)에서도 음표를 끊는다."""
    f0, conf, db = track["f0"], track["conf"], track["db"]
    midi = 69 + 12 * np.log2(np.maximum(f0, 1e-3) / 440.0)
    voiced = (conf >= conf_th) & (db >= db_th) & (f0 > 0)
    # 짧은 구멍 메우기 / 짧은 잡음 제거
    voiced = _close_gaps(voiced, 4)
    voiced = _remove_short(voiced, 6)

    split_frames = set()
    for t in split_times or []:
        split_frames.add(int(round(t / HOP_S)))

    notes: list[dict] = []
    cur: list[int] = []
    dev_count = 0

    def flush():
        nonlocal cur
        if len(cur) >= 6 and (len(cur) >= 15 or float(np.mean(conf[cur])) >= 0.6):  # 짧고 불확실한 음 제외
            vals = midi[cur]
            c = conf[cur]
            notes.append({
                "start": round(cur[0] * HOP_S, 3),
                "end": round((cur[-1] + 1) * HOP_S, 3),
                "pitch": _median(vals),
                "conf": float(np.mean(c)),
            })
        cur = []

    for i in range(len(midi)):
        if not voiced[i]:
            flush()
            dev_count = 0
            continue
        if cur:
            ref = _median(midi[cur[-25:]])  # 최근 250ms 기준
            if i in split_frames and len(cur) >= 6:
                flush()
                dev_count = 0
            elif abs(midi[i] - ref) > 0.75:
                dev_count += 1
                if dev_count >= 4:
                    # 벗어나기 시작한 지점에서 끊기
                    back = cur[-(dev_count - 1):] if dev_count > 1 else []
                    if back:
                        cur = cur[: -(dev_count - 1)]
                    flush()
                    cur = list(back)
                    dev_count = 0
            else:
                dev_count = 0
        cur.append(i)
    flush()

    # 아주 짧은 음(글리산도 조각)은 이웃에 붙이고, 같은 음 연속은 합침
    cleaned: list[dict] = []
    for nt in notes:
        dur = nt["end"] - nt["start"]
        if cleaned and dur < 0.09 and nt["start"] - cleaned[-1]["end"] < 0.03:
            cleaned[-1]["end"] = nt["end"]
            continue
        cleaned.append(nt)
    out = []
    for nt in cleaned:
        nt["midi"] = int(round(nt["pitch"]))
        if not (43 <= nt["midi"] <= 88):  # 사람 목소리 범위 밖(반주 잔향)은 제외
            continue
        if (out and out[-1]["midi"] == nt["midi"] and nt["start"] - out[-1]["end"] < 0.08
                and int(round(nt["start"] / HOP_S)) not in split_frames):
            out[-1]["end"] = nt["end"]
            continue
        out.append(nt)
    return [{"start": n["start"], "end": n["end"], "midi": n["midi"],
             "pitch": round(n["pitch"], 2), "conf": round(n["conf"], 3)} for n in out]


def _close_gaps(mask: np.ndarray, max_gap: int) -> np.ndarray:
    m = mask.copy()
    n = len(m)
    i = 0
    while i < n:
        if not m[i]:
            j = i
            while j < n and not m[j]:
                j += 1
            if 0 < i and j < n and j - i <= max_gap:
                m[i:j] = True
            i = j
        else:
            i += 1
    return m


def _remove_short(mask: np.ndarray, min_len: int) -> np.ndarray:
    m = mask.copy()
    n = len(m)
    i = 0
    while i < n:
        if m[i]:
            j = i
            while j < n and m[j]:
                j += 1
            if j - i < min_len:
                m[i:j] = False
            i = j
        else:
            i += 1
    return m
