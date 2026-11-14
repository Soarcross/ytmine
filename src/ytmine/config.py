"""Where ytmine keeps its files, and the few settings a user can change.

Everything lives in per-user directories chosen by platformdirs, so nothing is
written inside the repository or the install location. Two environment
variables override the defaults (handy for tests and portable installs):

    YTMINE_DATA_DIR   mined history, audio clips, exported decks
    YTMINE_CACHE_DIR  downloaded subtitles and full audio tracks
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "ytmine"
log = logging.getLogger(__name__)


@dataclass
class Settings:
    data_dir: Path
    cache_dir: Path
    language: str = "zh"
    padding: float = 0.3  # seconds of audio added before and after each clip
    ankiconnect_url: str = "http://127.0.0.1:8765"
    deck_name: str = "ytmine Mandarin"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def history_path(self) -> Path:
        return self.data_dir / "mined_history.json"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.cache_dir, self.media_dir, self.exports_dir):
            path.mkdir(parents=True, exist_ok=True)

    @classmethod
    def load(cls) -> Settings:
        """Build settings from the environment plus an optional config.json."""
        data_env = os.environ.get("YTMINE_DATA_DIR")
        cache_env = os.environ.get("YTMINE_CACHE_DIR")
        settings = cls(
            data_dir=Path(data_env) if data_env else platformdirs.user_data_path(APP_NAME, False),
            cache_dir=Path(cache_env)
            if cache_env
            else platformdirs.user_cache_path(APP_NAME, False),
        )
        settings._apply_config_file(config_path())
        return settings

    def _apply_config_file(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Ignoring unreadable config file %s: %s", path, exc)
            return
        if not isinstance(data, dict):
            log.warning("Ignoring config file %s: expected a JSON object", path)
            return
        if isinstance(data.get("padding"), (int, float)) and 0 <= data["padding"] <= 5:
            self.padding = float(data["padding"])
        for key in ("ankiconnect_url", "deck_name", "language"):
            if isinstance(data.get(key), str) and data[key].strip():
                setattr(self, key, data[key].strip())


def config_path() -> Path:
    return platformdirs.user_config_path(APP_NAME, False) / "config.json"
