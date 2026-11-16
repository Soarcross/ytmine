"""Let the browser extension start ytmine: a native messaging host.

An extension cannot start programs. What it can do is send a message to a "native messaging
host", a program the browser starts for it, provided the host is registered for that extension.
`ytmine --browser-start on` registers this module as the host. The extension then asks it to
start ytmine when the browser starts, and again whenever it finds the server not running.

The host answers one message, `{"cmd": "start"}`: it starts ytmine in the background (no window,
no browser tab) unless it already runs, waits until it answers, and replies with its port and the
pairing token. Only the ytmine extension can reach the host (the browser checks the origin in the
host manifest), so handing it the token is no different from the automatic pairing.

Registration writes, per browser, a small JSON manifest that points at a launcher script in
ytmine's config folder. On Windows the browser finds the manifest through a registry key under
HKEY_CURRENT_USER; on macOS and Linux through a folder in the browser's own profile directory.
`ytmine --browser-start off` removes all of it.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

from ytmine import instance
from ytmine.config import config_path
from ytmine.launcher import LauncherError, Places, launch_args, python_for_launch

HOST_NAME = "io.github.soarcross.ytmine"
# Fixed by the "key" in extension/manifest.json. Changing that key changes this ID.
CHROME_EXTENSION_ID = "cgojdfnmlfgkljdokdfdeaahblgkkgpm"
FIREFOX_EXTENSION_ID = "ytmine@example.invalid"  # browser_specific_settings in the manifest
DEFAULT_PORT = 8787
START_TIMEOUT = 20.0  # seconds to wait for a freshly started server to answer
MAX_MESSAGE = 64 * 1024

# Chromium-based browsers and where each looks for host manifests. Windows: registry keys under
# HKEY_CURRENT_USER. macOS: under ~/Library/Application Support. Linux: under ~/.config.
CHROMIUM_BROWSERS = {
    # name: (Windows registry key, macOS folder, Linux folder)
    "Chrome": ("Software\\Google\\Chrome", "Google/Chrome", "google-chrome"),
    "Chromium": ("Software\\Chromium", "Chromium", "chromium"),
    "Edge": ("Software\\Microsoft\\Edge", "Microsoft Edge", "microsoft-edge"),
    "Brave": (
        "Software\\BraveSoftware\\Brave-Browser",
        "BraveSoftware/Brave-Browser",
        "BraveSoftware/Brave-Browser",
    ),
}
FIREFOX_KEY = "Software\\Mozilla"


# -- the host itself ----------------------------------------------------------------------


def read_message(stream) -> dict | None:
    """One message: a 4-byte length in native byte order, then that much UTF-8 JSON."""
    header = stream.read(4)
    if len(header) < 4:
        return None
    (length,) = struct.unpack("=I", header)
    if length > MAX_MESSAGE:
        return None
    try:
        message = json.loads(stream.read(length).decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


def write_message(stream, message: dict) -> None:
    data = json.dumps(message).encode("utf-8")
    stream.write(struct.pack("=I", len(data)))
    stream.write(data)
    stream.flush()


def _spawn_server() -> None:
    """Start ytmine as a process of its own, so it outlives this host (the browser ends the
    host as soon as it has replied)."""
    system = Places.here().system
    command = [str(python_for_launch(system)), *launch_args(at_login=True)]
    options = dict(
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path.home()),
        close_fds=True,
    )
    if system != "Windows":
        subprocess.Popen(command, start_new_session=True, **options)
        return
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        # Leave the browser's job object too, if it allows that, so closing the browser does
        # not take ytmine with it.
        subprocess.Popen(
            command, creationflags=flags | subprocess.CREATE_BREAKAWAY_FROM_JOB, **options
        )
    except OSError:
        subprocess.Popen(command, creationflags=flags, **options)


def ensure_running(timeout: float = START_TIMEOUT) -> int | None:
    """The port of the running ytmine, starting it first if needed. None if it did not start."""
    port = instance.find_running(DEFAULT_PORT)
    if port is not None:
        return port
    _spawn_server()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.25)
        port = instance.find_running(DEFAULT_PORT)
        if port is not None:
            return port
    return None


def handle(message: dict | None) -> dict:
    if not message or message.get("cmd") != "start":
        return {"ok": False, "error": "unknown-command"}
    from ytmine.config import Settings
    from ytmine.pairing import load_or_create_token

    port = ensure_running()
    if port is None:
        return {"ok": False, "error": "did-not-start"}
    token = load_or_create_token(Settings.load().token_file)
    return {"ok": True, "port": port, "token": token}


def main() -> int:
    # stdout carries the protocol, so nothing else may be printed. The browser passes the
    # caller's origin (and on Windows a window handle) as arguments; they are not needed.
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    write_message(stdout, handle(read_message(stdin)))
    return 0


# -- registering it with the browsers -----------------------------------------------------


def host_dir() -> Path:
    return config_path().parent / "browser-host"


def _script_path(places: Places) -> Path:
    return host_dir() / ("ytmine-host.bat" if places.system == "Windows" else "ytmine-host")


def _script(places: Places) -> str:
    # python.exe, not pythonw.exe: the host needs stdin and stdout. The browser starts it with
    # no visible window.
    python = sys.executable  # kept as a string: tests use POSIX paths on Windows and back
    if places.system == "Windows":
        return f'@echo off\r\n"{python}" -m ytmine.nativehost %*\r\n'
    import shlex

    return f'#!/bin/sh\nexec {shlex.quote(str(python))} -m ytmine.nativehost "$@"\n'


def _manifest(script: Path, firefox: bool) -> str:
    manifest = {
        "name": HOST_NAME,
        "description": "Starts ytmine for its browser extension",
        "path": str(script),
        "type": "stdio",
    }
    if firefox:
        manifest["allowed_extensions"] = [FIREFOX_EXTENSION_ID]
    else:
        manifest["allowed_origins"] = [f"chrome-extension://{CHROME_EXTENSION_ID}/"]
    return json.dumps(manifest, indent=2) + "\n"


def _manifest_folders(places: Places) -> list[tuple[Path, bool]]:
    """(folder, is_firefox) for each browser installed for this user (macOS and Linux)."""
    if places.system == "Darwin":
        support = places.home / "Library" / "Application Support"
        found = [(support / mac, False) for _, mac, _ in CHROMIUM_BROWSERS.values()]
        found.append((support / "Mozilla", True))
    else:
        config = Path(places.env.get("XDG_CONFIG_HOME") or places.home / ".config")
        found = [(config / linux, False) for _, _, linux in CHROMIUM_BROWSERS.values()]
        found.append((places.home / ".mozilla", True))
    # Only browsers that have a profile folder; never create one for a browser that is absent.
    folder = "native-messaging-hosts" if places.system != "Darwin" else "NativeMessagingHosts"
    return [
        (base / (folder if firefox else "NativeMessagingHosts"), firefox)
        for base, firefox in found
        if base.is_dir()
    ]


class WindowsRegistry:
    """The few registry calls needed, under HKEY_CURRENT_USER. Tests swap in a dict."""

    def set(self, key: str, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
            winreg.SetValueEx(handle, "", 0, winreg.REG_SZ, value)

    def delete(self, key: str) -> bool:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            return False
        return True


def _registry_keys() -> list[tuple[str, bool]]:
    keys = [(base, False) for base, _, _ in CHROMIUM_BROWSERS.values()] + [(FIREFOX_KEY, True)]
    return [(f"{base}\\NativeMessagingHosts\\{HOST_NAME}", firefox) for base, firefox in keys]


def set_browser_start(on: bool, places: Places | None = None, registry=None) -> list[str]:
    """Register the host with every browser found (on), or remove it (off). Returns what was
    written or removed, for the message to the user."""
    places = places or Places.here()
    script = _script_path(places)
    done: list[str] = []
    if places.system == "Windows":
        registry = WindowsRegistry() if registry is None else registry
        chrome_manifest = host_dir() / f"{HOST_NAME}.json"
        firefox_manifest = host_dir() / f"{HOST_NAME}.firefox.json"
        if on:
            _write(script, _script(places))
            _write(chrome_manifest, _manifest(script, firefox=False))
            _write(firefox_manifest, _manifest(script, firefox=True))
        for key, firefox in _registry_keys():
            if on:
                registry.set(key, str(firefox_manifest if firefox else chrome_manifest))
                done.append("HKCU\\" + key)
            elif registry.delete(key):
                done.append("HKCU\\" + key)
        if not on:
            done += [str(p) for p in (chrome_manifest, firefox_manifest, script) if _remove(p)]
        return done

    folders = _manifest_folders(places)
    if on:
        if not folders:
            raise LauncherError(
                "No Chrome, Chromium, Edge, Brave or Firefox profile was found for this user."
            )
        _write(script, _script(places))
        script.chmod(0o755)
    for folder, firefox in folders:
        path = folder / f"{HOST_NAME}.json"
        if on:
            _write(path, _manifest(script, firefox))
            done.append(str(path))
        elif _remove(path):
            done.append(str(path))
    if not on and _remove(script):
        done.append(str(script))
    return done


def browser_start_enabled(places: Places | None = None) -> bool:
    return _script_path(places or Places.here()).exists()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def _remove(path: Path) -> bool:
    if path.exists():
        path.unlink()
        return True
    return False


if __name__ == "__main__":
    sys.exit(main())
