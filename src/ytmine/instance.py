"""One ytmine at a time: find the server that is already running, open it, or stop it.

A running server writes its port and process id to `server.json` in the config directory and
removes the file when it stops. The file is only a hint: a server counts as running only if it
answers /api/info like ytmine does, so a file left behind by a crash does no harm.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from ytmine.config import config_path
from ytmine.pairing import TOKEN_HEADER

HOST = "127.0.0.1"
# Talk to the local server directly, never through a proxy from the environment.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def run_file() -> Path:
    return config_path().parent / "server.json"


def record(port: int) -> None:
    """Note that this process serves ytmine on `port`."""
    path = run_file()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    except OSError:
        pass  # only a hint; probing the default port still finds us


def forget() -> None:
    """Remove the note, if it is ours."""
    path = run_file()
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def _saved_port() -> int | None:
    try:
        port = json.loads(run_file().read_text(encoding="utf-8")).get("port")
    except (OSError, ValueError, AttributeError):
        return None
    return port if isinstance(port, int) else None


def is_ytmine(port: int, timeout: float = 1.0) -> bool:
    """True if a ytmine server answers on this port."""
    try:
        with _opener.open(f"http://{HOST}:{port}/api/info", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "session_id" in data and "dictionary" in data


def find_running(port: int) -> int | None:
    """The port of a running ytmine: the one it noted down, else `port`. None if there is none."""
    candidates = [p for p in (_saved_port(), port) if p]
    for candidate in dict.fromkeys(candidates):
        if is_ytmine(candidate):
            return candidate
    return None


def request_stop(port: int, token: str, timeout: float = 3.0) -> bool:
    """Ask the server on `port` to shut down. Needs the pairing token."""
    request = urllib.request.Request(
        f"http://{HOST}:{port}/api/shutdown", method="POST", headers={TOKEN_HEADER: token}
    )
    try:
        with _opener.open(request, timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False
