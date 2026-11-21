"""Video metadata + download via yt-dlp (subprocess, no library import).

yt-dlp is called as a CLI so a missing / outdated Python binding can
never break the server, and so Twitch + YouTube are both supported
with the same code path. Only process videos the user has rights to
(see README legal note).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Best video <=1080p + best audio, merged to mp4. 1080p is all the
# vertical 1080x1920 render ever needs; higher resolutions only cost
# download time and disk on a small server.
FORMAT = "bv*[height<=1080]+ba/b[height<=1080]/b"


def _ytdlp_cmd() -> list[str]:
    """yt-dlp CLI if on PATH, else `python -m yt_dlp` (pip installs
    put the module on sys.path even when the script dir is not)."""
    if shutil.which("yt-dlp"):
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


def get_metadata(url: str) -> dict:
    """Fetch title/channel/thumbnail/duration WITHOUT downloading."""
    proc = _run(_ytdlp_cmd() + [
        "--dump-json", "--no-playlist", "--no-download",
        "--no-warnings", url,
    ])
    if proc.returncode != 0:
        raise RuntimeError(
            f"yt-dlp could not read this URL: {proc.stderr.strip()[-400:] or proc.stdout.strip()[-400:]}"
        )
    info = json.loads(proc.stdout)
    return {
        "title": info.get("title", ""),
        "channel": info.get("channel") or info.get("uploader", ""),
        "thumbnail": info.get("thumbnail", ""),
        "duration": float(info.get("duration") or 0),
        "id": info.get("id", ""),
        "webpage_url": info.get("webpage_url", url),
    }


def download(url: str, out_dir: str | Path) -> Path:
    """Download the video into out_dir/source.mp4. Returns its path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "source.mp4"
    proc = _run(_ytdlp_cmd() + [
        "-f", FORMAT, "--merge-output-format", "mp4",
        "--no-playlist", "--no-warnings",
        "-o", str(target), url,
    ])
    if proc.returncode != 0 or not target.exists():
        raise RuntimeError(f"yt-dlp download failed: {proc.stderr.strip()[-500:]}")
    return target


def probe(path: str | Path) -> dict:
    """ffprobe: width/height/duration of a local file."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr[-300:]}")
    data = json.loads(proc.stdout)
    stream = (data.get("streams") or [{}])[0]
    return {
        "width": int(stream.get("width") or 0),
        "height": int(stream.get("height") or 0),
        "duration": float((data.get("format") or {}).get("duration") or 0),
    }
