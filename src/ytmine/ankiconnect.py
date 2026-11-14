"""Send cards straight to a running Anki through the AnkiConnect add-on.

Only localhost is ever contacted. Sending works by asking Anki to import the same
.apkg that the Download button produces.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ADDON_CODE = "2055492159"
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


class AnkiConnectError(Exception):
    """Anki could not be reached, or it rejected a request."""


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


def import_package(package_path: Path, url: str) -> None:
    """Have Anki import an .apkg file, exactly as File > Import would.

    This is the same file the Download button gives you, so both routes use the same note
    types and the same stable GUIDs. Notes Anki already has are updated if ytmine's copy is
    newer (so edits made in Anki are kept), and new ones are added.

    Anki reads the file itself, from this computer's disk. That fails if Anki runs in a
    sandbox that cannot see the file (Flatpak, Snap); the error says so.
    """
    invoke(url, "version")  # fail fast with a clear message if Anki is not running
    path = str(package_path.resolve())
    if not invoke(url, "importPackage", path=path):
        raise AnkiConnectError(
            "Anki could not import the file. If Anki runs in a sandbox (Flatpak or Snap) it "
            f"cannot read {path}; use Download .apkg and import it from Anki instead."
        )
