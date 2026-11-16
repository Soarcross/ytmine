"""Cut short mp3 clips out of a downloaded audio track with ffmpeg."""

from __future__ import annotations

import subprocess
from pathlib import Path


class ClipError(Exception):
    """ffmpeg failed to produce a clip."""


# Subtitle cues often end early: a cue can stop at a pause in the middle of a sentence while
# the last words are spoken in the silence-on-screen gap before the next cue. So a clip may run
# this far past the cue's end into such a gap, but never into the next cue.
GAP_TAIL = 1.2  # seconds
NEXT_CUE_MARGIN = 0.05  # seconds kept clear before the next cue starts


def clip_window(
    start: float,
    end: float,
    padding: float = 0.3,
    max_duration: float | None = None,
    next_start: float | None = None,
) -> tuple[float, float]:
    """Return `(clip_start, clip_length)` in seconds for a line.

    The line is widened by `padding` on both sides. If the next cue starts later than that
    (`next_start`; None after the last line), the end also runs into the gap, up to `GAP_TAIL`
    after the line and never past the next cue. The start never goes below zero, and the end
    is clamped to `max_duration` (the audio length) if given.
    """
    clip_start = max(0.0, start - padding)
    clip_end = end + padding
    into_gap = end + GAP_TAIL
    if next_start is not None:
        into_gap = min(into_gap, next_start - NEXT_CUE_MARGIN)
    clip_end = max(clip_end, into_gap)
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
