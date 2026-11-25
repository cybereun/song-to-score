"""조성 추정과 코드 추정."""
from __future__ import annotations

import numpy as np

# Krumhansl-Kessler 조성 프로파일
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

CHORD_TYPES = {
    "": ([0, 4, 7], 0.0),
    "m": ([0, 3, 7], 0.0),
    "7": ([0, 4, 7, 10], 0.06),
    "m7": ([0, 3, 7, 10], 0.06),
    "maj7": ([0, 4, 7, 11], 0.08),
    "sus4": ([0, 5, 7], 0.2),
    "dim": ([0, 3, 6], 0.2),
}

MAJOR_SCALE = [0, 2, 4, 5, 7, 9, 11]
MINOR_SCALE = [0, 2, 3, 5, 7, 8, 10]


def estimate_key(chroma: np.ndarray, melody_pcs: np.ndarray | None = None) -> dict:
    c = np.asarray(chroma, dtype=float)
    c = c / (c.sum() + 1e-9)
    if melody_pcs is not None and melody_pcs.sum() > 0:
        c = c + 0.5 * melody_pcs / melody_pcs.sum()
    best = None
    for tonic in range(12):
        for mode, prof in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
            r = np.corrcoef(c, np.roll(prof, tonic))[0, 1]
            if best is None or r > best[0]:
                best = (r, tonic, mode)
    return {"tonic": int(best[1]), "mode": best[2], "confidence": round(float(best[0]), 3)}


def _templates():
    names, mats, pens, roots = [], [], [], []
    for root in range(12):
        for q, (ivs, pen) in CHORD_TYPES.items():
            v = np.zeros(12)
            for k, iv in enumerate(ivs):
                v[(root + iv) % 12] = 1.0 if k < 3 else 0.7
            v /= np.linalg.norm(v)
            names.append((root, q))
            mats.append(v)
            pens.append(pen)
            roots.append(root)
    return names, np.array(mats), np.array(pens), np.array(roots)


def estimate_chords(seg_chroma: np.ndarray, seg_bass: np.ndarray, seg_energy: np.ndarray,
                    key: dict, change_penalty: float = 0.12) -> list[tuple[int, str] | None]:
    """구간(보통 반 마디)별 크로마로 코드열을 고른다. 비터비로 잦은 변경을 억제."""
    names, T, pens, roots = _templates()
    scale = MAJOR_SCALE if key["mode"] == "major" else MINOR_SCALE
    diatonic = {(key["tonic"] + s) % 12 for s in scale}
    n = len(seg_chroma)
    if n == 0:
        return []
    S = np.zeros((n, len(names)))
    for i in range(n):
        c = np.asarray(seg_chroma[i], float)
        c = c / (np.linalg.norm(c) + 1e-9)
        b = np.asarray(seg_bass[i], float)
        b = b / (b.max() + 1e-9)
        sc = T @ c - pens
        sc += 0.3 * b[roots]
        for j, (root, q) in enumerate(names):
            tones = [(root + iv) % 12 for iv in CHORD_TYPES[q][0][:3]]
            out = sum(t not in diatonic for t in tones)
            sc[j] -= 0.05 * out
        S[i] = sc
    # 비터비
    dp = S[0].copy()
    back = np.zeros((n, len(names)), dtype=int)
    for i in range(1, n):
        stay = dp
        best_prev = int(np.argmax(dp))
        move = dp[best_prev] - change_penalty
        choose_move = move > stay
        back[i] = np.where(choose_move, best_prev, np.arange(len(names)))
        dp = np.where(choose_move, move, stay) + S[i]
    path = [int(np.argmax(dp))]
    for i in range(n - 1, 0, -1):
        path.append(int(back[i][path[-1]]))
    path.reverse()
    thr = 0.04 * max(float(np.max(seg_energy)), 1e-9)
    out = []
    for i, j in enumerate(path):
        out.append(None if seg_energy[i] < thr else names[j])
    return out
