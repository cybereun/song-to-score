"""템포·박 위치 추정과 박 단위 화성(크로마) 특징 추출."""
from __future__ import annotations

from pathlib import Path

import numpy as np

SR = 22050
HOP = 512


def analyze(mix: Path, accomp: Path, progress=lambda p, msg="": None) -> dict:
    import librosa

    y, _ = librosa.load(str(mix), sr=SR, mono=True)
    progress(0.2, "템포 분석 중")
    onset_env = librosa.onset.onset_strength(y=y, sr=SR, hop_length=HOP)
    tempo, beats = librosa.beat.beat_track(onset_envelope=onset_env, sr=SR, hop_length=HOP,
                                           start_bpm=100, tightness=120)
    tempo = float(np.atleast_1d(tempo)[0])
    beat_times = librosa.frames_to_time(beats, sr=SR, hop_length=HOP)
    beat_times = _fill_beats(beat_times, tempo, len(y) / SR)

    progress(0.5, "코드 분석 중")
    ya, _ = librosa.load(str(accomp), sr=SR, mono=True)
    harm = librosa.effects.harmonic(ya, margin=3.0)
    chroma = librosa.feature.chroma_cqt(y=harm, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C2"),
                                        n_octaves=5)
    bass = librosa.feature.chroma_cqt(y=harm, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C1"),
                                      n_octaves=2)
    energy = librosa.feature.rms(y=ya, hop_length=HOP)[0]
    onset_acc = librosa.onset.onset_strength(y=ya, sr=SR, hop_length=HOP)

    bf = librosa.time_to_frames(beat_times, sr=SR, hop_length=HOP)
    nfr = chroma.shape[1]
    bf = np.clip(bf, 0, nfr - 1)
    edges = np.concatenate([bf, [nfr]])
    beat_chroma, beat_bass, beat_energy, beat_onset = [], [], [], []
    for i in range(len(bf)):
        a, b = int(edges[i]), int(max(edges[i + 1], edges[i] + 1))
        beat_chroma.append(np.median(chroma[:, a:b], axis=1))
        beat_bass.append(np.median(bass[:, a:b], axis=1))
        beat_energy.append(float(np.mean(energy[min(a, len(energy) - 1):b])) if a < len(energy) else 0.0)
        k = min(a, len(onset_acc) - 1)
        beat_onset.append(float(onset_acc[max(0, k - 1):k + 2].max()))
    progress(1.0, "템포·코드 분석 완료")
    return {
        "tempo": round(tempo, 2),
        "beat_times": [round(float(t), 3) for t in beat_times],
        "beat_chroma": np.round(np.array(beat_chroma), 4).tolist(),
        "beat_bass": np.round(np.array(beat_bass), 4).tolist(),
        "beat_energy": np.round(np.array(beat_energy), 5).tolist(),
        "beat_onset": np.round(np.array(beat_onset), 4).tolist(),
        "global_chroma": np.round(chroma.mean(axis=1), 4).tolist(),
        "duration": round(len(y) / SR, 3),
    }


def _fill_beats(bt: np.ndarray, tempo: float, dur: float) -> np.ndarray:
    """곡 처음과 끝까지 박을 연장해 둔다."""
    period = 60.0 / tempo if tempo > 0 else 0.5
    if len(bt) < 2:
        return np.arange(0, dur + period, period)
    if len(bt) > 4:
        period = float(np.median(np.diff(bt)))
    head = []
    t = bt[0] - period
    while t > -period * 0.5:
        head.append(t)
        t -= period
    tail = []
    t = bt[-1] + period
    while t < dur + period:
        tail.append(t)
        t += period
    return np.concatenate([np.array(head[::-1]), bt, np.array(tail)])
