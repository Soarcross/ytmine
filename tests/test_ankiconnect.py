import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ytmine.ankiconnect import AnkiConnectError, invoke, send_cards


class FakeAnki:
    """A tiny AnkiConnect stand-in that records calls and rejects duplicate fronts."""

    def __init__(self):
        self.calls = []
        self.models = []
        self.fronts = set()
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
        if action == "modelNames":
            return list(self.models), None
        if action == "createModel":
            self.models.append(params["modelName"])
            return {}, None
        if action == "addNote":
            front = params["note"]["fields"]["Front"]
            if front in self.fronts:
                return None, "cannot create note because it is a duplicate"
            self.fronts.add(front)
            return 1, None
        return None, None

    def close(self):
        self.server.shutdown()


@pytest.fixture
def anki():
    fake = FakeAnki()
    yield fake
    fake.close()


def make_card(n=1):
    return {
        "id": f"v_{n}",
        "lang": "zh",
        "video_id": "abcdefghijk",
        "start": 1.0,
        "end": 2.0,
        "sentence": f"句子{n}",
        "pinyin": "",
        "english": "",
        "target": "",
        "audio_file": f"clip{n}.mp3",
    }


def test_send_cards_creates_model_uploads_audio_and_adds_notes(anki, tmp_path):
    (tmp_path / "clip1.mp3").write_bytes(b"mp3 bytes")
    result = send_cards([make_card(1)], tmp_path, "My Deck", anki.url)
    assert (result.added, result.skipped, result.errors) == (1, 0, [])
    actions = [c["action"] for c in anki.calls]
    assert actions[:4] == ["version", "modelNames", "createModel", "createDeck"]
    store = next(c for c in anki.calls if c["action"] == "storeMediaFile")
    assert base64.b64decode(store["params"]["data"]) == b"mp3 bytes"
    note = next(c for c in anki.calls if c["action"] == "addNote")["params"]["note"]
    assert note["deckName"] == "My Deck"
    assert note["fields"]["Audio"] == "[sound:clip1.mp3]"


def test_duplicates_are_skipped_not_failed(anki, tmp_path):
    send_cards([make_card(1)], tmp_path, "D", anki.url)
    result = send_cards([make_card(1), make_card(2)], tmp_path, "D", anki.url)
    assert (result.added, result.skipped) == (1, 1)


def test_unreachable_anki_gives_a_helpful_message():
    with pytest.raises(AnkiConnectError, match="Open Anki"):
        invoke("http://127.0.0.1:1", "version")


def test_non_local_urls_are_refused():
    with pytest.raises(AnkiConnectError, match="localhost"):
        invoke("http://example.com:8765", "version")
