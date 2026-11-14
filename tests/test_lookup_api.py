"""The /api/lookup route and the dictionary fields of /api/load."""

import pytest

from ytmine.app import Services, create_app
from ytmine.config import Settings
from ytmine.fetch import VideoData
from ytmine.lang import get_language
from ytmine.lang.cedict import build_database

ZH = """WEBVTT

00:00:02.000 --> 00:00:04.000
他说三个臭皮匠，顶个诸葛亮。
"""


def make_client(tmp_path, fixture_text, *, with_dictionary):
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    if with_dictionary:
        # Pre-build the cache so the app opens it without any download.
        cache_file = settings.cache_dir / "cedict" / "cedict.sqlite"
        build_database(fixture_text("cedict_mini.txt"), cache_file)
    services = Services(
        fetch_video=lambda url, cache: VideoData("abcdefghijk", "T", 60.0, ZH, "manual (zh-Hans)"),
        download_audio=lambda vid, cache: tmp_path / "none",
        find_ffmpeg=lambda: None,
    )
    client = create_app(settings, services).test_client()
    get_language("zh").wait_ready(10)
    return client


@pytest.fixture
def client(tmp_path, fixture_text):
    return make_client(tmp_path, fixture_text, with_dictionary=True)


def test_lookup_returns_entry_fields(client):
    data = client.get("/api/lookup", query_string={"q": "學習"}).get_json()
    entry = data["entries"][0]
    assert entry == {
        "headword": "学习",
        "alt_form": "學習",
        "reading": "xué xí",
        "definitions": ["to study; to learn"],
        "kind": "word",
    }
    assert data["dictionary"]["state"] == "ready"


def test_lookup_polyphone_and_kind(client):
    readings = [e["reading"] for e in client.get("/api/lookup?q=行").get_json()["entries"]]
    assert readings == ["xíng", "háng"]
    saying = client.get("/api/lookup", query_string={"q": "三个臭皮匠，顶个诸葛亮"}).get_json()
    assert saying["entries"][0]["kind"] == "suyu"


def test_lookup_needs_a_query(client):
    assert client.get("/api/lookup").status_code == 400
    assert client.get("/api/lookup?q=" + "字" * 100).status_code == 400


def test_load_returns_expression_spans(client):
    data = client.post("/api/load", json={"url": "https://youtu.be/abcdefghijk"}).get_json()
    line = data["lines"][0]
    spans = line["expressions"]
    assert [(s["text"], s["kind"]) for s in spans] == [("三个臭皮匠，顶个诸葛亮", "suyu")]
    assert line["text"][spans[0]["start"] : spans[0]["end"]] == spans[0]["text"]
    assert data["dictionary"]["state"] == "ready"


def test_app_works_without_a_dictionary(tmp_path, fixture_text):
    client = make_client(tmp_path, fixture_text, with_dictionary=False)  # download is blocked
    data = client.post("/api/load", json={"url": "https://youtu.be/abcdefghijk"}).get_json()
    assert data["dictionary"]["state"] == "unavailable"
    assert data["lines"][0]["expressions"] == []
    assert data["lines"][0]["tokens"]  # segmentation and pinyin still work
    lookup = client.get("/api/lookup?q=学习").get_json()
    assert lookup["entries"] == []
    assert "definitions are off" in lookup["dictionary"]["error"]
    assert client.get("/api/info").get_json()["dictionary"]["state"] == "unavailable"
