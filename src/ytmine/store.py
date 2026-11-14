"""Two small JSON files kept next to the mined history.

- caption fixes: corrections you made to a video's captions (auto-captions get words wrong),
  so they are still there the next time you load the same video.
- known words: words you marked as known, plus the data needed to colour words by status.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _read(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable file %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)  # atomic, so a crash never leaves a half-written file


def line_key(start: float) -> str:
    """Identify a line by its start time in milliseconds. Cleaning is deterministic, so the
    same video gives the same lines (and the same keys) every time it is loaded."""
    return str(int(round(start * 1000)))


class CaptionFixes:
    """video id -> {line key -> corrected text}."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, dict[str, str]] = {
            vid: fixes for vid, fixes in _read(path).items() if isinstance(fixes, dict)
        }

    def for_video(self, video_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._data.get(video_id, {}))

    def set(self, video_id: str, key: str, text: str) -> None:
        with self._lock:
            self._data.setdefault(video_id, {})[key] = text
            _write(self.path, self._data)

    def clear(self, video_id: str, key: str) -> None:
        with self._lock:
            fixes = self._data.get(video_id, {})
            if fixes.pop(key, None) is not None:
                if not fixes:
                    self._data.pop(video_id, None)
                _write(self.path, self._data)


class KnownWords:
    """Words marked as known by hand."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        words = _read(path).get("words", [])
        self._words: set[str] = {w for w in words if isinstance(w, str)}

    def all(self) -> set[str]:
        with self._lock:
            return set(self._words)

    def update(self, words: set[str], known: bool) -> None:
        with self._lock:
            if known:
                self._words |= words
            else:
                self._words -= words
            _write(self.path, {"words": sorted(self._words)})
