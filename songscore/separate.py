"""Demucs로 보컬 / 반주 분리."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf


def load_audio(path: Path, sr: int, mono: bool = False) -> np.ndarray:
    """오디오를 (채널, 샘플) float32 로 읽는다. mp3 등은 librosa(ffmpeg) 경유."""
    import librosa

    y, _ = librosa.load(str(path), sr=sr, mono=mono)
    if y.ndim == 1:
        y = y[None, :]
    return y.astype(np.float32)


def separate(src: Path, out_dir: Path, progress=lambda p, msg="": None) -> tuple[Path, Path]:
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = get_model("htdemucs")
    model.eval()
    sr = model.samplerate

    wav = load_audio(src, sr, mono=False)
    if wav.shape[0] == 1:
        wav = np.repeat(wav, 2, axis=0)
    wav = wav[:2]
    t = torch.from_numpy(wav)
    ref = t.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    t = (t - mean) / std

    progress(0.05, "보컬 분리 모델 실행 중")
    # Demucs 는 무작위 시간 이동(shift)을 써서 실행마다 결과가 조금씩 달라진다 → 고정
    import random

    random.seed(0)
    torch.manual_seed(0)
    with torch.no_grad():
        sources = apply_model(
            model, t[None], device=device, shifts=1, split=True, overlap=0.25, progress=False,
            segment=None,
        )[0]
    sources = sources * std + mean
    names = list(model.sources)  # drums, bass, other, vocals
    vocals = sources[names.index("vocals")].cpu().numpy()
    accomp = sum(sources[i] for i, n in enumerate(names) if n != "vocals").cpu().numpy()

    out_dir.mkdir(parents=True, exist_ok=True)
    v_path, a_path = out_dir / "vocals.wav", out_dir / "accomp.wav"
    sf.write(v_path, vocals.T, sr, subtype="PCM_16")
    sf.write(a_path, accomp.T, sr, subtype="PCM_16")
    del sources
    if device == "cuda":
        torch.cuda.empty_cache()
    progress(1.0, "보컬 분리 완료")
    return v_path, a_path
