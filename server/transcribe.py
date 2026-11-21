"""Audio extraction + faster-whisper transcription with word timestamps.

Word timestamps are the whole point: the v3 model requires captions
synced word-by-word (~0.1 s tolerance). Never use YouTube's grouped
VTT timings — they desync the captions (MODELO_FINAL.md).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .models import Transcript, TranscriptSegment, Word


def extract_audio(source: str | Path, out_wav: str | Path) -> Path:
    """Extract 16 kHz mono WAV for Whisper. Raises RuntimeError on failure."""
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(out_wav),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out_wav.exists():
        raise RuntimeError(f"ffmpeg audio extract failed: {proc.stderr[-500:]}")
    return out_wav


def transcribe(
    audio_path: str | Path, model_name: str = "small", language: str = "pt"
) -> Transcript:
    """Transcribe with faster-whisper, word timestamps on.

    Import is lazy so the server can start (and serve /health) on a
    machine where the model isn't downloaded yet; the first call
    downloads the model (~500 MB for ``small``) and is slow.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - missing dep
        raise RuntimeError(
            "faster-whisper is not installed. Run: pip install -r requirements.txt"
        ) from exc

    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
    )
    segments: list[TranscriptSegment] = []
    words: list[Word] = []
    for seg in segments_iter:
        seg_words = [
            Word(word=w.word, start=float(w.start), end=float(w.end))
            for w in (seg.words or [])
        ]
        words.extend(seg_words)
        segments.append(
            TranscriptSegment(
                start=float(seg.start), end=float(seg.end),
                text=seg.text.strip(), words=seg_words,
            )
        )
    return Transcript(
        language=info.language or language,
        duration=float(info.duration),
        segments=segments,
        words=words,
    )


def save_transcript(transcript: Transcript, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(transcript.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
