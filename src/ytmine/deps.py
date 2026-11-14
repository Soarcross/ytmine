"""Checks for external programs ytmine needs (currently just ffmpeg)."""

from __future__ import annotations

import platform
import shutil


def find_ffmpeg() -> str | None:
    """Return the path to ffmpeg, or None if it is not on PATH."""
    return shutil.which("ffmpeg")


def ffmpeg_install_help(system: str | None = None) -> str:
    """Install instructions for the current platform (or the one passed in)."""
    system = system or platform.system()
    if system == "Windows":
        return (
            "ffmpeg was not found. Install it with one of:\n"
            "  winget install Gyan.FFmpeg\n"
            "  choco install ffmpeg\n"
            "  scoop install ffmpeg\n"
            "Then open a new terminal so the updated PATH is picked up."
        )
    if system == "Darwin":
        return (
            "ffmpeg was not found. Install it with Homebrew:\n"
            "  brew install ffmpeg\n"
            "(MacPorts works too: sudo port install ffmpeg)"
        )
    return (
        "ffmpeg was not found. Install it with your package manager, for example:\n"
        "  Debian/Ubuntu:  sudo apt install ffmpeg\n"
        "  Fedora:         sudo dnf install ffmpeg-free\n"
        "  Arch:           sudo pacman -S ffmpeg\n"
        "  openSUSE:       sudo zypper install ffmpeg"
    )
