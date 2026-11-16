"""Command-line entry point: `ytmine` or `python -m ytmine`."""

from __future__ import annotations

import argparse
import logging
import os
import platform
import shutil
import socket
import sys
import threading
import webbrowser

from ytmine import __version__, instance
from ytmine.config import Settings
from ytmine.deps import ffmpeg_install_help, find_ffmpeg

DEFAULT_PORT = 8787  # not 8765, which AnkiConnect uses
LOG_LIMIT = 1_000_000  # bytes; a bigger log is set aside at start-up
# Where Homebrew and MacPorts put ffmpeg. Apps started from the Finder or at login on macOS get a
# bare PATH without them.
MAC_TOOL_DIRS = ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_port(host: str, preferred: int, explicit: bool) -> int | None:
    if explicit:
        return preferred if _port_is_free(host, preferred) else None
    for port in range(preferred, preferred + 20):
        if _port_is_free(host, port):
            return port
    return None


def _log_to_file(settings: Settings) -> None:
    """Send output to a log file, for runs with no console (shortcut, start at login)."""
    log = settings.cache_dir / "ytmine.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        if log.exists() and log.stat().st_size > LOG_LIMIT:
            log.replace(log.with_name("ytmine.old.log"))
        stream = open(log, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 (open until exit)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    sys.stdout = sys.stderr = stream


def _add_mac_tool_dirs() -> None:
    if platform.system() != "Darwin":
        return
    parts = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p]
    missing = [d for d in MAC_TOOL_DIRS if d not in parts and os.path.isdir(d)]
    os.environ["PATH"] = os.pathsep.join(parts + missing)


def _stop(settings: Settings, port: int) -> int:
    from ytmine.pairing import load_or_create_token

    running = instance.find_running(port)
    if running is None:
        print("ytmine is not running.")
        return 0
    if instance.request_stop(running, load_or_create_token(settings.token_file)):
        print(f"Stopped ytmine at http://{instance.HOST}:{running}/")
        return 0
    print("ytmine did not stop. Close it from where it was started.", file=sys.stderr)
    return 1


def _shortcut(create: bool) -> int:
    from ytmine import launcher

    try:
        paths = launcher.create_shortcut() if create else launcher.remove_shortcut()
    except (launcher.LauncherError, OSError) as exc:
        print(f"Could not {'add' if create else 'remove'} the shortcut: {exc}", file=sys.stderr)
        return 1
    if not create:
        print("Removed " + ", ".join(map(str, paths)) if paths else "There was no shortcut.")
        return 0
    print("Added a ytmine shortcut:")
    for path in paths:
        print(f"  {path}")
    print(
        "Click it to start ytmine. It runs in the background with no window and opens its page.\n"
        "Clicking it again while ytmine runs just opens the page. To stop ytmine: ytmine --stop"
    )
    return 0


def _autostart(on: bool) -> int:
    from ytmine import launcher

    try:
        paths = launcher.set_autostart(on)
    except (launcher.LauncherError, OSError) as exc:
        print(f"Could not change start at login: {exc}", file=sys.stderr)
        return 1
    if on:
        print(f"ytmine will start by itself, in the background, when you log in ({paths[0]}).")
        print("It opens no browser tab then. Open the page with the shortcut or the extension.")
        print("To start it now without waiting, run: ytmine")
    else:
        print("ytmine will no longer start at login." if paths else "Start at login was off.")
    return 0


def _browser_start(on: bool) -> int:
    from ytmine import launcher, nativehost

    try:
        done = nativehost.set_browser_start(on)
    except (launcher.LauncherError, OSError) as exc:
        print(f"Could not change start with the browser: {exc}", file=sys.stderr)
        return 1
    if on:
        print("The ytmine browser extension can now start ytmine by itself: when the browser")
        print("starts, and whenever it finds ytmine not running. Registered in:")
        for item in done:
            print(f"  {item}")
        print("Restart the browser (or reload the extension) for it to take effect.")
    else:
        print(
            "The extension will no longer start ytmine."
            if done
            else "Start with the browser was off."
        )
    return 0


def _shortcut_tip() -> str:
    """One line suggesting the shortcut, shown at start-up in a terminal until one exists."""
    from ytmine import launcher

    try:
        if launcher.shortcut_exists():
            return ""
    except OSError:
        return ""
    return "Tip: run `ytmine --create-shortcut` to start ytmine with a click next time."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ytmine", description="Sentence-mine Mandarin from YouTube videos into Anki."
    )
    parser.add_argument("--port", type=int, help=f"port to listen on (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser tab")
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help="delete downloaded audio and subtitles, then exit",
    )
    parser.add_argument(
        "--show-token",
        action="store_true",
        help="print the browser extension pairing token, then exit",
    )
    parser.add_argument("--stop", action="store_true", help="stop the ytmine that is running")
    parser.add_argument(
        "--create-shortcut",
        action="store_true",
        help="add a ytmine icon to the desktop and Start menu (Applications on macOS, "
        "the app menu on Linux) that starts ytmine without a terminal",
    )
    parser.add_argument(
        "--remove-shortcut", action="store_true", help="remove the icon --create-shortcut added"
    )
    parser.add_argument(
        "--autostart",
        choices=("on", "off"),
        help="start ytmine in the background whenever you log in (on), or stop doing that (off)",
    )
    parser.add_argument(
        "--browser-start",
        choices=("on", "off"),
        help="let the browser extension start ytmine when the browser starts (on), "
        "or stop that (off)",
    )
    parser.add_argument(
        "--background",
        action="store_true",
        help="write messages to a log file instead of the terminal "
        "(the shortcut and start at login use this)",
    )
    parser.add_argument("--version", action="version", version=f"ytmine {__version__}")
    args = parser.parse_args(argv)

    settings = Settings.load()
    # pythonw (the Windows shortcut) has no console at all: sys.stdout is None there.
    if args.background or sys.stdout is None or sys.stderr is None:
        _log_to_file(settings)
        _add_mac_tool_dirs()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if args.clear_cache:
        shutil.rmtree(settings.cache_dir, ignore_errors=True)
        print(f"Cleared {settings.cache_dir}")
        return 0

    if args.show_token:
        from ytmine.pairing import load_or_create_token

        print(load_or_create_token(settings.token_file))
        return 0

    if args.stop:
        return _stop(settings, args.port or DEFAULT_PORT)

    if args.create_shortcut or args.remove_shortcut:
        return _shortcut(create=args.create_shortcut)

    if args.autostart:
        return _autostart(args.autostart == "on")

    if args.browser_start:
        return _browser_start(args.browser_start == "on")

    # Started twice (say, from the shortcut while it already runs from login): open the running one.
    running = instance.find_running(args.port or DEFAULT_PORT)
    if running is not None and (args.port is None or running == args.port):
        url = f"http://{instance.HOST}:{running}/"
        print(f"ytmine is already running at {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    if find_ffmpeg() is None:
        print(ffmpeg_install_help(), file=sys.stderr)
        print("ytmine will still start, but mining needs ffmpeg.\n", file=sys.stderr)

    # Imported here so --version and --help stay fast.
    from werkzeug.serving import make_server

    from ytmine.app import create_app

    host = instance.HOST  # local only; never listen on the network
    port = _pick_port(host, args.port or DEFAULT_PORT, explicit=args.port is not None)
    if port is None:
        print("Could not find a free port. Try: ytmine --port 9000", file=sys.stderr)
        return 1

    app = create_app(settings)
    server = make_server(host, port, app, threaded=True)
    # `ytmine --stop` arrives through /api/shutdown. shutdown() waits for serve_forever() to
    # return, so it has to run on a thread of its own.
    app.config["SHUTDOWN"] = lambda: threading.Thread(target=server.shutdown, daemon=True).start()
    url = f"http://{host}:{port}/"
    print(f"ytmine {__version__} is running at {url}")
    print(f"Your data is stored in {settings.data_dir}")
    print("The browser extension connects by itself while the ytmine page is open.")
    print(f"(Manual pairing token, if you ever need it: {app.config['PAIRING_TOKEN']})")
    print("Press Ctrl+C to stop, or run: ytmine --stop")
    if not args.background and sys.stdout.isatty() and (tip := _shortcut_tip()):
        print(tip)
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    instance.record(port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
    finally:
        instance.forget()
        server.server_close()
    return 0
