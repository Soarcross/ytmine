"""Send cards straight to a running Anki through the AnkiConnect add-on.

Only localhost is ever contacted. Audio is sent as base64 data so it also works
when Anki runs in a sandbox (Flatpak, Snap) that cannot read our files.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from ytmine.export import (
    BACK_TEMPLATE,
    CARD_NAME,
    CSS,
    FIELDS,
    FRONT_TEMPLATE,
    MODEL_NAME,
    note_fields,
)
from ytmine.lang import get_language

ADDON_CODE = "2055492159"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class AnkiConnectError(Exception):
    """Anki could not be reached, or it rejected a request."""


@dataclass
class SendResult:
    added: int = 0
    skipped: int = 0  # already in Anki
    errors: list[str] = field(default_factory=list)


def _check_local(url: str) -> None:
    host = urlsplit(url).hostname
    if host not in _LOCAL_HOSTS:
        raise AnkiConnectError(
            f"AnkiConnect URL must point at this computer (localhost), not '{host}'."
        )


def invoke(url: str, action: str, **params):
    """Call one AnkiConnect action and return its result."""
    _check_local(url)
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode("utf-8")
    request = urllib.request.Request(url, payload, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise AnkiConnectError(
            f"Could not reach Anki at {url}. Open Anki and make sure the AnkiConnect "
            f"add-on is installed (add-on code {ADDON_CODE})."
        ) from exc
    except ValueError as exc:
        raise AnkiConnectError(f"{url} did not answer like AnkiConnect.") from exc
    if not isinstance(body, dict) or "error" not in body:
        raise AnkiConnectError(f"{url} did not answer like AnkiConnect.")
    if body["error"]:
        raise AnkiConnectError(str(body["error"]))
    return body.get("result")


def _ensure_model(url: str) -> None:
    if MODEL_NAME in invoke(url, "modelNames"):
        return
    invoke(
        url,
        "createModel",
        modelName=MODEL_NAME,
        inOrderFields=FIELDS,
        css=CSS,
        isCloze=False,
        cardTemplates=[{"Name": CARD_NAME, "Front": FRONT_TEMPLATE, "Back": BACK_TEMPLATE}],
    )


def send_cards(cards: list[dict], media_dir: Path, deck_name: str, url: str) -> SendResult:
    """Add each card as a note. Notes Anki already has (same front) are skipped."""
    invoke(url, "version")  # fail fast with a clear message if Anki is not running
    _ensure_model(url)
    invoke(url, "createDeck", deck=deck_name)

    result = SendResult()
    for card in cards:
        fields = note_fields(card, get_language(card.get("lang", "zh")))
        audio_name = card.get("audio_file")
        audio_path = media_dir / audio_name if audio_name else None
        try:
            if audio_path is not None and audio_path.is_file():
                data = base64.b64encode(audio_path.read_bytes()).decode("ascii")
                invoke(url, "storeMediaFile", filename=audio_name, data=data)
            else:
                fields["Audio"] = ""
            invoke(
                url,
                "addNote",
                note={
                    "deckName": deck_name,
                    "modelName": MODEL_NAME,
                    "fields": fields,
                    "tags": ["ytmine"],
                    "options": {"allowDuplicate": False, "duplicateScope": "deck"},
                },
            )
            result.added += 1
        except AnkiConnectError as exc:
            if "duplicate" in str(exc).lower():
                result.skipped += 1
            else:
                result.errors.append(f"{card['sentence']}: {exc}")
    return result
