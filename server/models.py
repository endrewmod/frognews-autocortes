"""Pydantic models for FrogNews AutoCortes render server.

These match the export JSON produced by the AutoCortes web app
(frognews-autocortes artifact): a render package with the cut window,
jump-cut segments, impact zooms and caption cues, all in seconds.

Time conventions (important — the v3 pipeline is built on them):
- ``start`` / ``end`` and ``segments`` are in SOURCE timeline seconds
  (seconds into the downloaded video / live).
- ``impacts`` and ``captions`` are in FINAL timeline seconds by default
  (seconds into the finished Short, after jump cuts), exactly as in
  build_v3.py / clip-v2.ass. If a client sends them in source time it
  must set ``timeline="source"`` and pipeline.py maps them via the
  segment offsets.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Word(BaseModel):
    word: str
    start: float = Field(ge=0)
    end: float = Field(ge=0)


class TranscriptSegment(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    text: str = ""
    words: list[Word] = []


class Transcript(BaseModel):
    language: str = "pt"
    duration: float = 0.0
    segments: list[TranscriptSegment] = []
    words: list[Word] = []


class CutSegment(BaseModel):
    """One kept chunk of source video (a jump-cut segment).

    ``punch_start`` / ``punch_end`` are frame indices *inside* this
    segment (30 fps), exactly as in build_v3.py. ``None`` = no punch,
    static zoom 1.00 for the whole segment.
    """

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    label: str = ""
    punch_start: Optional[int] = None
    punch_end: Optional[int] = None
    peak: float = 1.09


class Impact(BaseModel):
    """A zoom punch. ``start``/``end`` are in final-timeline seconds
    unless ``timeline="source"``."""

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    peak: float = 1.09  # 1.09 strong, 1.07 light — v3 model, do not change
    text: str = ""
    timeline: Literal["final", "source"] = "final"


class CaptionCue(BaseModel):
    """One caption group: 2-3 words, shown exactly while spoken.
    ``start``/``end`` are in final-timeline seconds by default.
    ``text`` may already contain ASS yellow tags
    ``{\\c&H00FFFF&}word{\\c&HFFFFFF&}``; if not, pipeline.py
    highlights keywords itself."""

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
    timeline: Literal["final", "source"] = "final"


class FaceBox(BaseModel):
    """Facecam crop as fractions of the source frame (0.0-1.0).
    Overrides the streamer preset when given."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)


class RenderRequest(BaseModel):
    """Render input. Accepts the web-app export JSON directly, and also
    a bare ``{start, end, ...}`` body. Either ``segments`` or a single
    ``start``/``end`` window is required; segments win when both given."""

    start: float = Field(default=0.0, ge=0)
    end: float = Field(default=0.0, ge=0)
    streamer: str = "davy"  # davy | alanzoka | default
    face_box: Optional[FaceBox] = None
    segments: list[CutSegment] = []
    impacts: list[Impact] = []
    captions: list[CaptionCue] = []
    words: list[Word] = []  # word timestamps; used to derive segments/captions
    title: str = ""
    url: str = ""

    def window(self) -> tuple[float, float]:
        if self.segments:
            return min(s.start for s in self.segments), max(
                s.end for s in self.segments
            )
        return self.start, self.end


class ExportPayload(BaseModel):
    """Wrapper the web app may send: the render request nested under
    ``cut`` / ``render`` / ``export``, plus job context. main.py
    unwraps it into a RenderRequest."""

    job_id: Optional[str] = None
    url: Optional[str] = None
    cut: Optional[RenderRequest] = None
    render: Optional[RenderRequest] = None
    export: Optional[RenderRequest] = None


class JobCreate(BaseModel):
    url: str


class Job(BaseModel):
    id: str
    url: str
    status: str = "created"
    step: str = "created"
    progress: int = 0  # 0-100
    title: str = ""
    channel: str = ""
    thumbnail: str = ""
    duration: float = 0.0
    error: str = ""
    source_path: str = ""
    audio_path: str = ""
    output_path: str = ""
    output_url: str = ""
    created_at: str = ""


class Suggestion(BaseModel):
    start: float
    end: float
    score: int = Field(ge=0, le=100)
    hook: str = ""
    reason: list[str] = []
    transcript_excerpt: str = ""


class SuggestResponse(BaseModel):
    job_id: str
    suggestions: list[Suggestion] = []
    llm_used: bool = False
