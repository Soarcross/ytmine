"""Command-line entry point: `ytmine` or `python -m ytmine`."""

from __future__ import annotations

import argparse
import logging
import shutil
import socket
import sys
import threading
import webbrowser

from ytmine import __version__
from ytmine.config import Settings
from ytmine.deps import ffmpeg_install_help, find_ffmpeg

DEFAULT_PORT = 8787  # not 8765, which AnkiConnect uses


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
    parser.add_argument("--version", action="version", version=f"ytmine {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    settings = Settings.load()

    if args.clear_cache:
        shutil.rmtree(settings.cache_dir, ignore_errors=True)
        print(f"Cleared {settings.cache_dir}")
        return 0

    if args.show_token:
        from ytmine.pairing import load_or_create_token

        print(load_or_create_token(settings.token_file))
        return 0

    if find_ffmpeg() is None:
        print(ffmpeg_install_help(), file=sys.stderr)
        print("ytmine will still start, but mining needs ffmpeg.\n", file=sys.stderr)

    # Imported here so --version and --help stay fast.
    from werkzeug.serving import make_server

    from ytmine.app import create_app

    host = "127.0.0.1"  # local only; never listen on the network
    port = _pick_port(host, args.port or DEFAULT_PORT, explicit=args.port is not None)
    if port is None:
        print("Could not find a free port. Try: ytmine --port 9000", file=sys.stderr)
        return 1

    app = create_app(settings)
    server = make_server(host, port, app, threaded=True)
    url = f"http://{host}:{port}/"
    print(f"ytmine {__version__} is running at {url}")
    print(f"Your data is stored in {settings.data_dir}")
    print(f"Browser extension pairing token: {app.config['PAIRING_TOKEN']}")
    print(
        "(paste it into the extension's options page once; `ytmine --show-token` prints it again)"
    )
    print("Press Ctrl+C to stop.")
    if not args.no_browser:
        threading.Timer(0.8, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")
    return 0
