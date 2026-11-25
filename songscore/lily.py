"""악보 모델 → LilyPond 원고 → PDF/MIDI."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .arrange import MODES, Event, Score, guitar_shape, shape_notes
from .config import find_lilypond

SHARP = ["c", "cis", "d", "dis", "e", "f", "fis", "g", "gis", "a", "ais", "b"]
FLAT = ["c", "des", "d", "es", "e", "f", "ges", "g", "as", "a", "bes", "b"]
DUR = {16: "1", 12: "2.", 8: "2", 6: "4.", 4: "4", 3: "8.", 2: "8", 1: "16"}
QUAL = {"": "", "m": ":m", "7": ":7", "m7": ":m7", "maj7": ":maj7", "sus4": ":sus4", "dim": ":dim"}
GROUP = {"4/4": 8, "3/4": 12, "2/4": 8, "6/8": 6}
FONT = "Malgun Gothic"


def pname(midi: int, flats: bool) -> str:
    name = (FLAT if flats else SHARP)[midi % 12]
    octave = midi // 12 - 1
    marks = octave - 3
    return name + ("'" * marks if marks > 0 else "," * (-marks))


def root_name(pc: int, flats: bool) -> str:
    return (FLAT if flats else SHARP)[pc % 12]


def _aligned(pos: int, d: int) -> bool:
    if d == 16:
        return pos == 0
    if d in (12, 8):
        return pos % 4 == 0
    if d in (6, 4, 3):
        return pos % 2 == 0
    return True


def split_len(pos: int, length: int, group: int) -> list[int]:
    out = []
    while length > 0:
        cap = length if pos % group == 0 else min(length, group - pos % group)
        for d in (16, 12, 8, 6, 4, 3, 2, 1):
            if d <= cap and _aligned(pos, d):
                break
        out.append(d)
        pos += d
        length -= d
    return out


def _chord_token(ev: Event, flats: bool, with_strings: bool) -> str:
    if len(ev.pitches) == 1 and not with_strings:
        return pname(ev.pitches[0], flats)
    parts = []
    for k, p in enumerate(ev.pitches):
        s = pname(p, flats)
        if with_strings and ev.strings:
            s += f"\\{ev.strings[k]}"
        parts.append(s)
    return "<" + " ".join(parts) + ">"


def _tag(kind: str, nid: int, k: int = 0) -> str:
    """SVG 에서 찾을 수 있게 음표/가사에 id 를 붙인다 (재생 표시·클릭 수정용)."""
    ident = f"{kind}{nid}" + (f"_{k}" if k else "")
    cls = {"n": "mn", "l": "ly"}[kind]
    return f"\\tweak output-attributes #'((id . \"{ident}\") (class . \"{cls}\")) "


def voice_music(events: list[Event], sc: Score, with_strings: bool = False,
                breaks: bool = False, ids: bool = False) -> tuple[str, list[tuple[str, int]]]:
    """음표열을 마디별 LilyPond 문자열로. 가사 음절 리스트도 함께 돌려준다."""
    bar, group = sc.bar_units, GROUP.get(sc.time_sig, 8)
    total = sc.n_bars * bar
    evs = sorted((e for e in events if e.length > 0), key=lambda e: e.start)
    # 겹침 제거
    clean: list[Event] = []
    for e in evs:
        if clean and e.start < clean[-1].start + clean[-1].length:
            clean[-1].length = e.start - clean[-1].start
            if clean[-1].length <= 0:
                clean.pop()
        clean.append(Event(e.start, min(e.length, total - e.start), e.pitches, e.lyric, e.strings,
                           e.nid, e.t0, e.t1))

    # 마디별 조각(시작, 길이, 이벤트, 첫조각여부)
    tokens_by_bar: list[list[str]] = [[] for _ in range(sc.n_bars)]
    filled = [0] * sc.n_bars
    lyrics: list[tuple[str, int]] = []

    def put_rest(start: int, length: int):
        while length > 0:
            b = start // bar
            pos = start % bar
            seg = min(length, bar - pos)
            if pos == 0 and seg == bar:
                tokens_by_bar[b].append("R" + DUR_BAR[sc.time_sig])
            else:
                for d in split_len(pos, seg, group):
                    tokens_by_bar[b].append("r" + DUR[d])
            start += seg
            length -= seg

    cursor = 0
    for e in clean:
        if e.start > cursor:
            put_rest(cursor, e.start - cursor)
        tok = _chord_token(e, sc.flats, with_strings)
        start, length = e.start, e.length
        pieces = []
        while length > 0:
            b = start // bar
            pos = start % bar
            seg = min(length, bar - pos)
            for d in split_len(pos, seg, group):
                pieces.append((b, d))
            start += seg
            length -= seg
        for k, (b, d) in enumerate(pieces):
            t = tok + DUR[d] + ("~" if k < len(pieces) - 1 else "")
            if ids and e.nid >= 0:
                t = _tag("n", e.nid, k) + t
            tokens_by_bar[b].append(t)
        lyrics.append((e.lyric, e.nid))
        cursor = e.start + e.length
    if cursor < total:
        put_rest(cursor, total - cursor)

    lines = []
    for b, toks in enumerate(tokens_by_bar):
        s = " ".join(toks) + " |"
        if breaks and (b + 1) % sc.bars_per_line == 0 and b + 1 < sc.n_bars:
            s += " \\break"
        lines.append(s)
    return "\n  ".join(lines), lyrics


DUR_BAR = {"4/4": "1", "3/4": "2.", "2/4": "2", "6/8": "2."}


def chord_music(sc: Score) -> str:
    toks = []
    cursor = 0
    for start, length, ch in sc.chords:
        if start > cursor:
            toks += _skips(cursor, start - cursor, sc)
        if ch is None:
            toks += _skips(start, length, sc, rest=True)
        else:
            root, q = ch
            durs = _plain_durs(start, length, sc)
            first = True
            for d in durs:
                if first:
                    toks.append(f"{root_name(root, sc.flats)}{DUR[d]}{QUAL[q]}")
                    first = False
                else:
                    toks.append(f"{root_name(root, sc.flats)}{DUR[d]}{QUAL[q]}")
        cursor = start + length
    return " ".join(toks)


def _plain_durs(start, length, sc):
    out = []
    bar = sc.bar_units
    while length > 0:
        pos = start % bar
        seg = min(length, bar - pos)
        out += split_len(pos, seg, GROUP.get(sc.time_sig, 8))
        start += seg
        length -= seg
    return out


def _skips(start, length, sc, rest=False):
    return [("r" if rest else "s") + DUR[d] for d in _plain_durs(start, length, sc)]


def fret_music(sc: Score) -> str:
    toks = []
    cursor = 0
    for start, length, ch in sc.chords:
        if start > cursor:
            toks += _skips(cursor, start - cursor, sc)
        durs = _plain_durs(start, length, sc)
        if ch is None:
            toks += ["s" + DUR[d] for d in durs]
        else:
            pitches, strings = shape_notes(guitar_shape(*ch))
            chord = "<" + " ".join(f"{pname(p, sc.flats)}\\{s}" for p, s in zip(pitches, strings)) + ">"
            toks.append(chord + DUR[durs[0]])
            toks += ["s" + DUR[d] for d in durs[1:]]
        cursor = start + length
    return " ".join(toks)


def _esc(s: str) -> str:
    return s.replace("\\", "").replace('"', "'")


def lyric_music(lyrics: list[tuple[str, int]], ids: bool = True) -> str:
    # 가사 없는 음표는 \skip 으로 건너뛰어 앞뒤 단어 간격이 유지되게 한다
    out = []
    for text, nid in lyrics:
        if not text:
            out.append("\\skip 1")
        else:
            out.append((_tag("l", nid) if ids and nid >= 0 else "") + f'"{_esc(text)}"')
    return " ".join(out)


def key_cmd(sc: Score) -> str:
    return f"\\key {root_name(sc.key_tonic, sc.flats)} \\{'major' if sc.key_mode == 'major' else 'minor'}"


def build_ly(sc: Score) -> str:
    head_tempo = "4." if sc.time_sig == "6/8" else "4"
    glob = f"{key_cmd(sc)} \\time {sc.time_sig} \\tempo {head_tempo} = {sc.bpm}"
    mel, lyr = voice_music(sc.melody, sc, breaks=True, ids=True)
    chords = chord_music(sc)
    show_lyr = sc.show_lyrics and any(t for t, _ in lyr)

    lyrics_ctx = f'\\new Lyrics \\lyricsto "mel" {{ {lyric_music(lyr)} }}' if show_lyr else ""
    chord_ctx = f"\\new ChordNames \\chordmode {{ \\set chordChanges = ##t {chords} }}"
    parts = []
    if sc.mode in ("lead",):
        parts = [chord_ctx,
                 f'\\new Staff = "up" {{ \\clef treble {glob} \\new Voice = "mel" {{ {mel} }} }}',
                 lyrics_ctx]
    elif sc.mode.startswith("piano"):
        low, _ = voice_music(sc.lower, sc)
        parts = [chord_ctx,
                 "\\new PianoStaff <<",
                 f'  \\new Staff = "up" {{ \\clef treble {glob} \\new Voice = "mel" {{ {mel} }} }}',
                 f"  {lyrics_ctx}",
                 f'  \\new Staff = "down" {{ \\clef bass {glob} {low} }}',
                 ">>"]
    elif sc.mode == "guitar_melody":
        parts = [chord_ctx,
                 f"\\new FretBoards {{ \\set chordChanges = ##t {fret_music(sc)} }}",
                 f'\\new Staff = "up" {{ \\clef "treble_8" {glob} \\new Voice = "mel" {{ {mel} }} }}',
                 lyrics_ctx,
                 f"\\new TabStaff {{ {glob} {mel} }}"]
    elif sc.mode == "guitar_chords":
        strum, _ = voice_music(sc.lower, sc, with_strings=True)
        # 노래 멜로디는 원래 음높이(한 옥타브 위)로 보여 준다
        mel_up = voice_music([Event(e.start, e.length, [e.pitches[0] + 12], e.lyric, nid=e.nid, t0=e.t0, t1=e.t1)
                              for e in sc.melody], sc, breaks=True, ids=True)[0]
        parts = [chord_ctx,
                 f"\\new FretBoards {{ \\set chordChanges = ##t {fret_music(sc)} }}",
                 f'\\new Staff = "up" \\with {{ instrumentName = "노래" }} '
                 f'{{ \\clef treble {glob} \\new Voice = "mel" {{ {mel_up} }} }}',
                 lyrics_ctx,
                 f'\\new TabStaff \\with {{ instrumentName = "기타" }} {{ {glob} {strum} }}']

    subtitle = f"{MODES.get(sc.mode, '')}  ·  {sc.key_label}"
    lyrics_page = _lyrics_markup(sc) if sc.lyrics_page and any(l.strip() for l in sc.lyrics_lines) else ""
    return f"""\\version "2.24.4"
\\paper {{
  #(set-paper-size "a4")
  #(define fonts (set-global-fonts #:roman "{FONT}" #:sans "{FONT}" #:typewriter "{FONT}" #:factor (/ staff-height pt 20)))
  top-margin = 12\\mm
  bottom-margin = 12\\mm
  left-margin = 15\\mm
  right-margin = 15\\mm
  system-system-spacing.basic-distance = #14
  markup-system-spacing.basic-distance = #10
  ragged-last-bottom = ##t
  print-first-page-number = ##t
  oddHeaderMarkup = \\markup \\fill-line {{ "" \\unless \\on-first-page \\fromproperty #'header:title \\if \\should-print-page-number \\fromproperty #'page:page-number-string }}
  evenHeaderMarkup = \\markup \\fill-line {{ \\if \\should-print-page-number \\fromproperty #'page:page-number-string \\unless \\on-first-page \\fromproperty #'header:title "" }}
}}
\\header {{
  title = "{_esc(sc.title)}"
  subtitle = "{_esc(subtitle)}"
  composer = "{_esc(sc.artist)}"
  tagline = ##f
}}
\\score {{
  <<
  {chr(10).join(p for p in parts if p)}
  >>
  \\layout {{
    \\context {{ \\Score \\override NonMusicalPaperColumn.line-break-permission = ##f }}
    \\context {{ \\Lyrics \\override LyricText.font-size = #0.5 \\override LyricSpace.minimum-distance = #1.2 }}
    \\context {{ \\ChordNames \\override ChordName.font-size = #1.5 }}
  }}
  \\midi {{ }}
}}
{lyrics_page}
"""


def _lyrics_markup(sc: Score) -> str:
    lines = [l for l in sc.lyrics_lines]
    half = (len(lines) + 1) // 2 if len(lines) > 34 else len(lines)
    cols = [lines[:half], lines[half:]] if half < len(lines) else [lines]

    def col(ls):
        return "\\column { " + " ".join(f'\\line {{ "{_esc(l)}" }}' if l else "\\vspace #0.5" for l in ls) + " }"

    body = " \\hspace #4 ".join(col(c) for c in cols)
    return f"""\\pageBreak
\\markup {{ \\fill-line {{ \\fontsize #4 \\bold "가사 · {_esc(sc.title)}" }} }}
\\markup {{ \\vspace #1.5 }}
\\markup {{ \\fontsize #1.5 \\override #'(baseline-skip . 4.2) \\fill-line {{ \\line {{ {body} }} }} }}
"""


def _run_lily(args: list[str]) -> subprocess.CompletedProcess:
    exe = find_lilypond()
    if not exe:
        raise RuntimeError("LilyPond를 찾을 수 없습니다. 설치.bat 을 먼저 실행하세요.")
    return subprocess.run(
        [exe, "-dno-point-and-click", *args], capture_output=True, text=True, encoding="utf-8",
        errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def render(sc: Score, out_dir: Path, name: str = "score") -> dict:
    """화면 미리보기용 SVG(쪽별)와 MIDI 를 만든다. PDF 는 저장·인쇄할 때 make_pdf 로 만든다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    ly = out_dir / f"{name}.ly"
    ly.write_text(build_ly(sc), encoding="utf-8")
    proc = _run_lily(["-dbackend=svg", "-o", str(out_dir / name), str(ly)])
    pages = sorted(out_dir.glob(f"{name}-*.svg"), key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    if not pages and (out_dir / f"{name}.svg").exists():
        pages = [out_dir / f"{name}.svg"]
    if proc.returncode != 0 or not pages:
        raise RuntimeError("악보 조판 실패:\n" + proc.stderr[-2000:])
    midi = out_dir / f"{name}.midi"
    if not midi.exists():
        midi = out_dir / f"{name}.mid"
    return {"pages": pages, "midi": midi if midi.exists() else None, "ly": ly, "log": proc.stderr[-3000:]}


def make_pdf(out_dir: Path, name: str = "score") -> Path:
    pdf = out_dir / f"{name}.pdf"
    ly = out_dir / f"{name}.ly"
    if pdf.exists() and pdf.stat().st_mtime >= ly.stat().st_mtime:
        return pdf
    proc = _run_lily(["-o", str(out_dir / name), str(ly)])
    if proc.returncode != 0 or not pdf.exists():
        raise RuntimeError("PDF 만들기 실패:\n" + proc.stderr[-2000:])
    return pdf
