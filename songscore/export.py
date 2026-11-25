"""MusicXML 내보내기 (MuseScore 등에서 열어 직접 편집용)."""
from __future__ import annotations

from pathlib import Path

from .arrange import Score

QUAL_XML = {"": "", "m": "m", "7": "7", "m7": "m7", "maj7": "maj7", "sus4": "sus4", "dim": "dim"}
NAMES_SHARP = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
NAMES_FLAT = ["C", "D-", "D", "E-", "E", "F", "G-", "G", "A-", "A", "B-", "B"]


def write_musicxml(sc: Score, path: Path) -> Path:
    from music21 import chord, clef, harmony, key, metadata, meter, note, stream, tempo

    names = NAMES_FLAT if sc.flats else NAMES_SHARP
    ql = 0.25  # 16분음표

    def make_part(events, is_melody, clef_obj):
        p = stream.Part()
        p.insert(0, clef_obj)
        p.insert(0, key.Key(names[sc.key_tonic].replace("-", "-"), sc.key_mode))
        p.insert(0, meter.TimeSignature(sc.time_sig))
        if is_melody:
            p.insert(0, tempo.MetronomeMark(number=sc.bpm))
            for start, length, ch in sc.chords:
                if ch is None:
                    continue
                root, q = ch
                try:
                    cs = harmony.ChordSymbol(names[root] + QUAL_XML[q])
                    cs.writeAsChord = False
                    p.insert(start * ql, cs)
                except Exception:
                    pass
        for ev in events:
            if not ev.pitches:
                continue
            if len(ev.pitches) == 1:
                n = note.Note(ev.pitches[0])
            else:
                n = chord.Chord(ev.pitches)
            n.quarterLength = ev.length * ql
            if is_melody and ev.lyric:
                n.lyric = ev.lyric
            p.insert(ev.start * ql, n)
        return p.makeNotation(inPlace=False)

    s = stream.Score()
    s.metadata = metadata.Metadata(title=sc.title, composer=sc.artist)
    mel_events = sc.melody
    s.insert(0, make_part(mel_events, True, clef.TrebleClef()))
    if sc.lower and sc.mode.startswith("piano"):
        s.insert(0, make_part(sc.lower, False, clef.BassClef()))
    s.write("musicxml", fp=str(path))
    return path
