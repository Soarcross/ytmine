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
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

APP_NAME = "ytmine"
log = logging.getLogger(__name__)

DEFAULT_CARD_TYPES = {
    "listening": True,
    "reading": True,
    "vocab_recognition": True,
    "vocab_production": False,
}
DECK_SPLITS = ("none", "channel", "video")


@dataclass
class Settings:
    data_dir: Path
    cache_dir: Path
    language: str = "zh"
    padding: float = 0.3  # seconds of audio added before and after each clip
    ankiconnect_url: str = "http://127.0.0.1:8765"
    deck_name: str = "ytmine Mandarin"
    # Which Anki card types to generate. Listening and Reading are the two sentence cards,
    # Recognition and Production the two vocab cards. Production is off by default.
    card_types: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_CARD_TYPES))
    deck_split: str = "none"  # none, channel or video: extra subdecks under Sentences
    config_file: Path | None = None  # where changes made in the web UI are saved

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
        settings.config_file = config_path()
        settings._apply_config_file(settings.config_file)
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
        if data.get("deck_split") in DECK_SPLITS:
            self.deck_split = data["deck_split"]
        if isinstance(data.get("card_types"), dict):
            for key in DEFAULT_CARD_TYPES:
                if isinstance(data["card_types"].get(key), bool):
                    self.card_types[key] = data["card_types"][key]

    def save_card_settings(self) -> None:
        """Write the card options back into config.json, keeping any other keys the user set."""
        if self.config_file is None:
            return
        data: dict = {}
        if self.config_file.is_file():
            try:
                loaded = json.loads(self.config_file.read_text(encoding="utf-8"))
                data = loaded if isinstance(loaded, dict) else {}
            except (OSError, ValueError):
                data = {}
        data.update(
            card_types=self.card_types, deck_split=self.deck_split, deck_name=self.deck_name
        )
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_file.with_name(self.config_file.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.config_file)


def config_path() -> Path:
    return platformdirs.user_config_path(APP_NAME, False) / "config.json"
