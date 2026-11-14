import shutil
import subprocess

import pytest

from ytmine.clips import ClipError, clip_window, cut_clip


def test_clip_window_adds_padding_both_sides():
    start, length = clip_window(10.0, 12.0, padding=0.3)
    assert start == pytest.approx(9.7)
    assert length == pytest.approx(2.6)


def test_clip_window_never_starts_before_zero():
    start, length = clip_window(0.1, 2.0, padding=0.3)
    assert start == 0.0
    assert length == pytest.approx(2.3)


def test_clip_window_clamps_to_audio_length():
    start, length = clip_window(58.0, 60.0, padding=0.3, max_duration=60.0)
    assert start == pytest.approx(57.7)
    assert length == pytest.approx(2.3)


def test_clip_window_past_the_end_is_empty():
    _, length = clip_window(70.0, 72.0, padding=0.3, max_duration=60.0)
    assert length == 0.0


def test_cut_clip_rejects_empty_window(tmp_path):
    with pytest.raises(ClipError):
        cut_clip("ffmpeg", tmp_path / "in.mp3", tmp_path / "out.mp3", 5.0, 0.0)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_cut_clip_with_real_ffmpeg(tmp_path):
    source = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            str(source),
        ],
        check=True,
    )
    out = tmp_path / "clip.mp3"
    cut_clip("ffmpeg", source, out, 1.0, 1.0)
    assert out.stat().st_size > 0
