"""반주 트랙의 음 받아쓰기 (Spotify Basic Pitch, ONNX 모델)."""
from __future__ import annotations

import logging
from pathlib import Path


def transcribe_accomp(accomp: Path, progress=lambda p, msg="": None) -> list[dict]:
    logging.getLogger().setLevel(logging.ERROR)
    try:
        from basic_pitch import ICASSP_2022_MODEL_PATH
        from basic_pitch.inference import Model, predict
    except Exception:
        return []
    onnx = Path(ICASSP_2022_MODEL_PATH).with_suffix(".onnx")
    if not onnx.exists():
        onnx = Path(ICASSP_2022_MODEL_PATH).parent / "nmp.onnx"
    progress(0.1, "반주 음 받아쓰는 중")
    try:
        model = Model(str(onnx))
        _, _, events = predict(
            str(accomp), model, onset_threshold=0.5, frame_threshold=0.3,
            minimum_note_length=100, minimum_frequency=55, maximum_frequency=1400,
            multiple_pitch_bends=False, melodia_trick=True,
        )
    except Exception as exc:  # 반주 받아쓰기는 선택 기능이므로 실패해도 계속
        logging.error("accomp transcription failed: %s", exc)
        return []
    progress(1.0, "반주 받아쓰기 완료")
    return [
        {"start": round(float(s), 3), "end": round(float(e), 3), "midi": int(p), "amp": round(float(a), 3)}
        for s, e, p, a, *_ in events
    ]
