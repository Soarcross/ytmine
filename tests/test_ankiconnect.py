import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ytmine.ankiconnect import AnkiConnectError, import_package, invoke


class FakeAnki:
    """A tiny AnkiConnect stand-in that records calls and answers importPackage."""

    def __init__(self):
        self.calls = []
        self.import_ok = True
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.calls.append(body)
                result, error = outer.handle(body["action"], body["params"])
                data = json.dumps({"result": result, "error": error}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, action, params):
        if action == "version":
            return 6, None
        if action == "importPackage":
            return self.import_ok, None
        return None, None

    def close(self):
        self.server.shutdown()


@pytest.fixture
def anki():
    fake = FakeAnki()
    yield fake
    fake.close()


def test_import_package_asks_anki_to_import_the_file(anki, tmp_path):
    package = tmp_path / "deck.apkg"
    package.write_bytes(b"zip")
    import_package(package, anki.url)
    assert [c["action"] for c in anki.calls] == ["version", "importPackage"]
    assert anki.calls[1]["params"]["path"] == str(package.resolve())


def test_import_refused_by_anki_explains_the_sandbox_case(anki, tmp_path):
    anki.import_ok = False
    package = tmp_path / "deck.apkg"
    package.write_bytes(b"zip")
    with pytest.raises(AnkiConnectError, match="Flatpak"):
        import_package(package, anki.url)


def test_unreachable_anki_gives_a_helpful_message():
    with pytest.raises(AnkiConnectError, match="Open Anki"):
        invoke("http://127.0.0.1:1", "version")


def test_non_local_urls_are_refused():
    with pytest.raises(AnkiConnectError, match="localhost"):
        invoke("http://example.com:8765", "version")
