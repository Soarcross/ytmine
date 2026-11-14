"""The pairing token that lets the browser extension talk to the local server.

The server only answers its own page. A browser extension is a different origin, so it has to
prove it is the one the user set up: it sends this token in a header. The token is random,
created on first run, kept in the user's config directory and printed at startup, and the user
pastes it into the extension's options page once.

Only requests whose Origin is an extension (chrome-extension:// or moz-extension://) can use
the token. A web page, youtube.com included, never gets in with it.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-Ytmine-Token"
_EXTENSION_SCHEMES = ("chrome-extension://", "moz-extension://")


def is_extension_origin(origin: str) -> bool:
    """True for the Origin of a Chrome or Firefox extension page or service worker."""
    return origin.startswith(_EXTENSION_SCHEMES) and len(origin) > max(map(len, _EXTENSION_SCHEMES))


def new_token() -> str:
    return secrets.token_urlsafe(32)


def load_or_create_token(path: Path | None) -> str:
    """Read the saved token, or make one and save it. With no path the token lives in memory."""
    if path is None:
        return new_token()
    try:
        saved = path.read_text(encoding="utf-8").strip()
        if len(saved) >= 20:
            return saved
    except OSError:
        pass
    token = new_token()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Owner-only where the OS supports it (no effect on Windows, whose profile is private).
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    except OSError as exc:
        log.warning(
            "Could not save the pairing token to %s (%s). It lasts until ytmine stops.", path, exc
        )
    return token


def token_matches(given: str | None, expected: str) -> bool:
    """Constant-time comparison, so the token cannot be guessed from response timing."""
    return bool(given) and hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))
