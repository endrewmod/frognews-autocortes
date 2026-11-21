"""Generalized FrogNews v3 render pipeline.

Faithful to ~/workspace/frognews-teste/build_v3.py + MODELO_FINAL.md
— do NOT redesign the model:

- Vertical 1080x1920, 30 fps, H.264 + AAC, loudnorm I=-16 TP=-1.5
- Facecam on top 1080x640, gameplay below 1080x1280 (built here from
  the landscape source; build_v3.py assumed an already-composed base)
- Subtitles: DejaVu Sans Bold 76, white with black outline, 2-3 words
  per cue, keywords in yellow, word-level sync. Swear words stay in
  the AUDIO but are never written in the caption text.
- Jump cuts on speech pauses; zoom 1.00 static EXCEPT a short
  trapezoid punch (0.2 s ramps, peak 1.09 strong / 1.07 light) only
  on impacts, centred on face + gameplay together.

Two-stage render:
  1. build_base() — landscape source -> vertical 1080x1920 base.
  2. render_cut() — jump-cut segments + zoom punches + loudnorm +
     ASS captions -> final Short.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .downloader import probe
from .models import CaptionCue, CutSegment, FaceBox, Impact, RenderRequest, Word

FPS = 30
RAMP_FRAMES = 6  # 0.2 s at 30 fps, v3 model
WIDTH, HEIGHT = 1080, 1920
FACE_H, GAME_H = 640, 1280

# Facecam position per streamer, as fractions of the source frame.
# davy: top-right (face crop 372x256 @1544,72 in a 1920x1080 source,
#   from build_part2.py). default: same as davy.
# alanzoka: right side, middle / lower-right (verified in the
#   FrogNews research notes — NOT top-right).
STREAMER_PRESETS: dict[str, FaceBox] = {
    "davy": FaceBox(x=0.804, y=0.067, w=0.194, h=0.237),
    "default": FaceBox(x=0.804, y=0.067, w=0.194, h=0.237),
    "alanzoka": FaceBox(x=0.78, y=0.48, w=0.20, h=0.28),
}

# Words never written in captions (audio keeps them).
SWEARS = {
    "caralho", "porra", "merda", "foda", "foder", "fodido", "fodida",
    "puta", "cacete", "krl", "caramba", "desgraça",
}
# Keywords highlighted yellow in captions ({\c&H00FFFF&}...).
KEYWORDS = [
    "tentáculo", "alien", "loucura", "maluquice", "goat",
    "easter egg", "easter eggs", "nave mãe", "gta 6", "lúcia",
]

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shorts,DejaVu Sans,76,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,40,40,280,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def fmt_ass(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


# ---------------------------------------------------------------- captions
def censor_text(text: str) -> str:
    """Replace swear words with '...' in caption TEXT only."""

    def repl(m: re.Match) -> str:
        word = m.group(0)
        core = re.sub(r"[^A-Za-zÀ-ÿ]", "", word).lower()
        return "..." if core in SWEARS else word

    return re.sub(r"[A-Za-zÀ-ÿ]+", repl, text)


def highlight_keywords(text: str) -> str:
    """Wrap keywords in ASS yellow tags. No-op if tags already present."""
    if "{\\c" in text:
        return text
    out = text
    for kw in sorted(KEYWORDS, key=len, reverse=True):
        out = re.sub(
            rf"(?i)\b({re.escape(kw)})\b",
            r"{\\c&H00FFFF&}\1{\\c&HFFFFFF&}",
            out,
        )
    return out


def prepare_cue_text(text: str) -> str:
    return highlight_keywords(censor_text(text))


def group_words_into_cues(
    words: list[Word], max_words: int = 3
) -> list[CaptionCue]:
    """Group word timestamps into 2-3 word cues (source timeline)."""
    cues: list[CaptionCue] = []
    buf: list[Word] = []

    def flush() -> None:
        if buf:
            cues.append(CaptionCue(
                start=buf[0].start, end=buf[-1].end,
                text=" ".join(w.word for w in buf), timeline="source",
            ))
            buf.clear()

    for w in words:
        if buf and (w.start - buf[-1].end > 0.45 or len(buf) >= max_words):
            flush()
        buf.append(w)
        if buf and re.search(r"[.!?…]$", w.word.strip()):
            flush()
    flush()
    return cues


# ---------------------------------------------------------------- segments
def derive_segments(
    start: float, end: float, words: list[Word],
    min_pause: float = 0.55, min_seg: float = 0.8,
) -> list[CutSegment]:
    """Jump-cut segments: keep speech, drop pauses >= min_pause.
    Never cut mid-word — boundaries sit on word edges."""
    win = [w for w in words if w.end > start and w.start < end]
    if not win:
        return [CutSegment(start=start, end=end)]
    segs: list[CutSegment] = []
    seg_start = max(start, win[0].start)
    prev_end = win[0].end
    for w in win[1:]:
        if w.start - prev_end >= min_pause:
            if prev_end - seg_start >= min_seg:
                segs.append(CutSegment(start=seg_start, end=prev_end))
            seg_start = w.start
        prev_end = max(prev_end, w.end)
    if prev_end - seg_start >= min_seg:
        segs.append(CutSegment(start=seg_start, end=min(end, prev_end)))
    return segs or [CutSegment(start=start, end=end)]


def segment_offsets(segments: list[CutSegment]) -> list[tuple[float, float, float]]:
    """(src_start, src_end, final_start - src_start) per segment."""
    out, t_final = [], 0.0
    for s in segments:
        out.append((s.start, s.end, t_final - s.start))
        t_final += s.end - s.start
    return out


def to_final(src: float, offsets: list[tuple[float, float, float]]) -> float | None:
    for s, e, off in offsets:
        if s - 0.05 <= src <= e + 0.05:
            return src + off
    return None


def total_duration(segments: list[CutSegment]) -> float:
    return sum(s.end - s.start for s in segments)


# ---------------------------------------------------------------- zoom
def zoom_expr(a: int | None, b: int | None, peak: float) -> str:
    """Trapezoid punch from build_v3.py: 1.00 outside [a,b], 0.2 s
    linear ramps to `peak` inside. `a`/`b` are frame indices in the
    segment; None = static 1.00."""
    if a is None or b is None:
        return "1"
    return (
        f"1+({peak}-1)*clip(min(min((on-{a})/{RAMP_FRAMES},1),"
        f"({b}-on)/{RAMP_FRAMES}),0,1)"
    )


# ---------------------------------------------------------------- base
def build_base(
    source: str | Path, out: str | Path,
    streamer: str = "davy", face_box: FaceBox | None = None,
) -> Path:
    """Landscape source -> vertical 1080x1920 base (face top / game bottom).

    Face: crop per streamer preset, scale to 1080x640.
    Gameplay: centre crop to 1080:1280 aspect (912x1080 in a 1920x1080
    source, as in build_part2.py), scale to 1080x1280.
    Already-vertical sources are just scaled to 1080x1920.
    """
    source, out = Path(source), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    info = probe(source)
    w, h = info["width"], info["height"]
    if w == 0 or h == 0:
        raise RuntimeError(f"could not probe {source}")
    if h >= w:  # already vertical
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-r", str(FPS), str(out),
        ]
    else:
        box = face_box or STREAMER_PRESETS.get(streamer.lower(), STREAMER_PRESETS["default"])
        fx, fy = int(box.x * w), int(box.y * h)
        fw, fh = int(box.w * w), int(box.h * h)
        # Clamp the face crop inside the frame.
        fw, fh = min(fw, w - fx), min(fh, h - fy)
        gw = int(h * WIDTH / GAME_H)  # 912 for a 1080-high source
        gx = max(0, (w - gw) // 2)
        fc = (
            f"[0:v]split=2[fs][gs];"
            f"[fs]crop={fw}:{fh}:{fx}:{fy},scale={WIDTH}:{FACE_H}:flags=lanczos,"
            f"fps={FPS},format=yuv420p[face];"
            f"[gs]crop={gw}:{h}:{gx}:0,scale={WIDTH}:{GAME_H}:flags=lanczos,"
            f"fps={FPS},format=yuv420p[game];"
            f"[face][game]vstack=inputs=2,format=yuv420p[v]"
        )
        cmd = [
            "ffmpeg", "-y", "-v", "error", "-i", str(source),
            "-filter_complex", fc,
            "-map", "[v]", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k", "-r", str(FPS), str(out),
        ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"build_base failed: {proc.stderr[-600:]}")
    return out


# ---------------------------------------------------------------- ASS
def build_ass(cues: list[CaptionCue], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [ASS_HEADER.rstrip("\n")]
    for cue in cues:
        if cue.end <= cue.start:
            continue
        lines.append(
            f"Dialogue: 0,{fmt_ass(cue.start)},{fmt_ass(cue.end)},Shorts,,0,0,0,,"
            f"{prepare_cue_text(cue.text)}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------- render
def _apply_impacts_to_segments(
    segments: list[CutSegment], impacts: list[Impact],
    offsets: list[tuple[float, float, float]],
) -> list[CutSegment]:
    """Fill each segment's punch frames from final-timeline impacts.
    Impacts given in source time are mapped through the offsets first.
    A segment keeps its strongest overlapping impact; segments with an
    explicit punch already set keep it."""
    final_impacts: list[tuple[float, float, float]] = []
    for imp in impacts:
        if imp.timeline == "source":
            fs, fe = to_final(imp.start, offsets), to_final(imp.end, offsets)
            if fs is None or fe is None:
                continue
            final_impacts.append((fs, fe, imp.peak))
        else:
            final_impacts.append((imp.start, imp.end, imp.peak))

    out: list[CutSegment] = []
    t_final = 0.0
    for seg in segments:
        seg_final_start, seg_final_end = t_final, t_final + (seg.end - seg.start)
        t_final = seg_final_end
        if seg.punch_start is not None and seg.punch_end is not None:
            out.append(seg)
            continue
        best: tuple[int, int, float] | None = None
        for fs, fe, peak in final_impacts:
            if fe <= seg_final_start or fs >= seg_final_end:
                continue
            a = max(0, round((max(fs, seg_final_start) - seg_final_start) * FPS))
            b = min(
                round((seg.end - seg.start) * FPS),
                round((min(fe, seg_final_end) - seg_final_start) * FPS),
            )
            if b > a and (best is None or peak > best[2]):
                best = (a, b, peak)
        if best:
            out.append(seg.model_copy(update={
                "punch_start": best[0], "punch_end": best[1], "peak": best[2],
            }))
        else:
            out.append(seg)
    return out


def _final_captions(
    cut: RenderRequest, offsets: list[tuple[float, float, float]]
) -> list[CaptionCue]:
    """Captions in final-timeline seconds, ready for the ASS file."""
    if cut.captions:
        cues: list[CaptionCue] = []
        for cue in cut.captions:
            if cue.timeline == "source":
                fs, fe = to_final(cue.start, offsets), to_final(cue.end, offsets)
                if fs is None or fe is None:
                    continue
                cues.append(CaptionCue(start=fs, end=fe, text=cue.text, timeline="final"))
            else:
                cues.append(cue)
        return cues
    if cut.words:
        cues = []
        for cue in group_words_into_cues(cut.words):
            fs, fe = to_final(cue.start, offsets), to_final(cue.end, offsets)
            if fs is not None and fe is not None and fe > fs:
                cues.append(CaptionCue(start=fs, end=fe, text=cue.text, timeline="final"))
        return cues
    return []


def render_cut(
    source: str | Path, job_dir: str | Path, cut: RenderRequest,
    output_name: str = "output.mp4",
) -> dict:
    """Full v3 render. Returns {output_path, duration, segments, captions}."""
    import os

    job_dir = Path(job_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    source = Path(source)
    if not source.exists():
        raise RuntimeError(f"source video not found: {source}")

    # 1. Vertical base (face top / gameplay bottom).
    base = build_base(source, job_dir / "base.mp4", cut.streamer, cut.face_box)

    # 2. Segments: explicit > derived from words > single window.
    if cut.segments:
        segments = sorted(cut.segments, key=lambda s: s.start)
    elif cut.words:
        segments = derive_segments(cut.start, cut.end, cut.words)
    elif cut.end > cut.start:
        segments = [CutSegment(start=cut.start, end=cut.end)]
    else:
        raise ValueError("render needs segments or a start/end window")
    segments = [s for s in segments if s.end > s.start]
    if not segments:
        raise ValueError("no usable segments in this cut")
    offsets = segment_offsets(segments)

    # 3. Impacts -> per-segment trapezoid punches; captions -> ASS.
    segments = _apply_impacts_to_segments(segments, cut.impacts, offsets)
    cues = _final_captions(cut, offsets)
    build_ass(cues, job_dir / "captions.ass")

    # 4. filter_complex, exactly as build_v3.py (trim + zoompan per
    #    segment, concat, loudnorm, subtitles), run from job_dir so
    #    the ASS filename needs no escaping.
    parts: list[str] = []
    for i, seg in enumerate(segments):
        z = zoom_expr(seg.punch_start, seg.punch_end, seg.peak)
        parts.append(
            f"[0:v]trim=start={seg.start:.2f}:end={seg.end:.2f},setpts=PTS-STARTPTS,"
            f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:"
            f"s={WIDTH}x{HEIGHT}:fps={FPS},format=yuv420p[v{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={seg.start:.2f}:end={seg.end:.2f},"
            f"asetpts=PTS-STARTPTS[a{i}]"
        )
    concat_in = "".join(f"[v{i}][a{i}]" for i in range(len(segments)))
    parts.append(
        f"{concat_in}concat=n={len(segments)}:v=1:a=1[vc][ac];"
        f"[ac]loudnorm=I=-16:TP=-1.5:LRA=11[aout];"
        f"[vc]subtitles=captions.ass[vout]"
    )
    output = job_dir / output_name
    cmd = [
        "ffmpeg", "-y", "-v", "error", "-i", "base.mp4",
        "-filter_complex", ";".join(parts),
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", output_name,
    ]
    timeout = int(os.environ.get("RENDER_TIMEOUT_SECONDS", "1200"))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=str(job_dir))
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"ffmpeg render failed: {proc.stderr[-800:]}")

    return {
        "output_path": str(output),
        "duration": round(total_duration(segments), 2),
        "segments": len(segments),
        "captions": len(cues),
        "punches": sum(1 for s in segments if s.punch_start is not None),
    }
