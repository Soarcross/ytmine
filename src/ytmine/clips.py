"""Cut short mp3 clips out of a downloaded audio track with ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path


class ClipError(Exception):
    """ffmpeg failed to produce a clip."""


def clip_window(
    start: float,
    end: float,
    padding: float = 0.3,
    max_duration: float | None = None,
) -> tuple[float, float]:
    """Return `(clip_start, clip_length)` in seconds for a line.

    The line is widened by `padding` on both sides. The start never goes below
    zero, and the end is clamped to `max_duration` (the audio length) if given.
    """
    clip_start = max(0.0, start - padding)
    clip_end = end + padding
    if max_duration is not None:
        clip_end = min(clip_end, max_duration)
    return clip_start, max(0.0, clip_end - clip_start)


def cut_clip(ffmpeg: str, source: Path, dest: Path, start: float, length: float) -> None:
    """Write `length` seconds of `source`, beginning at `start`, to `dest` as mono mp3."""
    if length <= 0:
        raise ClipError("The clip would be empty (start time is past the end of the audio).")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{length:.3f}",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        "-map_metadata",
        "-1",
        str(dest),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ClipError(f"Could not run ffmpeg: {exc}") from exc
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        detail = (
            result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "unknown error"
        )
        raise ClipError(f"ffmpeg could not cut the clip: {detail}")
