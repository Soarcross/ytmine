"""The store of mined cards, saved as mined_history.json in the user data directory.

It does two jobs: it stops the same sentence being mined twice, and it keeps
every card (with its audio file name) so a deck can be exported at any time,
even after a restart.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)
FORMAT_VERSION = 1


def sentence_key(text: str) -> str:
    """Normalize a sentence for duplicate detection (ignores spacing, punctuation, case)."""
    text = unicodedata.normalize("NFKC", text).lower()
    return "".join(ch for ch in text if unicodedata.category(ch)[0] in ("L", "N"))


class History:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._cards: list[dict] = self._load()
        self._keys: dict[str, int] = {}  # sentence key -> number of cards with it
        for card in self._cards:
            self._count(card, 1)

    def _count(self, card: dict, delta: int) -> None:
        key = sentence_key(card["sentence"])
        self._keys[key] = self._keys.get(key, 0) + delta
        if self._keys[key] <= 0:
            del self._keys[key]

    # -- persistence -------------------------------------------------------

    def _load(self) -> list[dict]:
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            cards = data["cards"]
            if not isinstance(cards, list):
                raise TypeError("cards must be a list")
            return cards
        except (OSError, ValueError, KeyError, TypeError) as exc:
            backup = self.path.with_name(self.path.name + ".corrupt")
            log.warning("Could not read %s (%s). Moving it to %s.", self.path, exc, backup)
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": FORMAT_VERSION, "cards": self._cards}
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)  # atomic, so a crash never leaves a half-written file

    # -- queries -----------------------------------------------------------

    def cards(self, session_id: str | None = None) -> list[dict]:
        """Cards oldest first; pass `session_id` to get only that session's cards."""
        with self._lock:
            return [
                dict(c)
                for c in self._cards
                if session_id is None or c.get("session_id") == session_id
            ]

    def get(self, card_id: str) -> dict | None:
        with self._lock:
            card = next((c for c in self._cards if c["id"] == card_id), None)
            return dict(card) if card else None

    def has_sentence(self, text: str) -> bool:
        with self._lock:
            return sentence_key(text) in self._keys

    def find_by_sentence(self, text: str) -> dict | None:
        key = sentence_key(text)
        with self._lock:
            card = next((c for c in self._cards if sentence_key(c["sentence"]) == key), None)
            return dict(card) if card else None

    # -- changes -----------------------------------------------------------

    def add(self, card: dict) -> None:
        with self._lock:
            card.setdefault("modified_at", time.time())
            self._cards.append(card)
            self._count(card, 1)
            self._save()

    def update(self, card_id: str, changes: dict) -> dict | None:
        with self._lock:
            card = next((c for c in self._cards if c["id"] == card_id), None)
            if card is None:
                return None
            self._count(card, -1)
            card.update(changes, modified_at=time.time())
            self._count(card, 1)
            self._save()
            return dict(card)

    def delete(self, card_id: str) -> dict | None:
        """Remove a card. Its sentence can be mined again afterwards."""
        with self._lock:
            for i, card in enumerate(self._cards):
                if card["id"] == card_id:
                    removed = self._cards.pop(i)
                    self._count(removed, -1)
                    self._save()
                    return removed
            return None
