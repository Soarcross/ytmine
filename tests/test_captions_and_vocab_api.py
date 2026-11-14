"""Mining an expression span, fixing a caption, and the mined and known word lists."""

import pytest

from ytmine.app import Services, create_app
from ytmine.config import Settings
from ytmine.fetch import VideoData
from ytmine.lang import get_language
from ytmine.lang.cedict import build_database

ZH = """WEBVTT

00:00:02.000 --> 00:00:04.000
他说三个臭皮匠，顶个诸葛亮。

00:00:05.000 --> 00:00:07.000
我喜欢学习中文。
"""
SAYING = "三个臭皮匠，顶个诸葛亮"


def make_app(tmp_path, fixture_text):
    audio = tmp_path / "audio.webm"
    audio.write_bytes(b"x")
    settings = Settings(data_dir=tmp_path / "data", cache_dir=tmp_path / "cache")
    cache_file = settings.cache_dir / "cedict" / "cedict.sqlite"
    if not cache_file.exists():
        build_database(fixture_text("cedict_mini.txt"), cache_file)
    services = Services(
        fetch_video=lambda url, cache: VideoData(
            "abcdefghijk", "T", 60.0, ZH, "auto-captions (zh)", channel="C"
        ),
        download_audio=lambda vid, cache: audio,
        cut_clip=lambda ffmpeg, source, dest, start, length: dest.write_bytes(b"mp3"),
        find_ffmpeg=lambda: "ffmpeg",
    )
    client = create_app(settings, services).test_client()
    get_language("zh").wait_ready(10)
    return client


@pytest.fixture
def client(tmp_path, fixture_text):
    return make_app(tmp_path, fixture_text)


def load(client):
    return client.post("/api/load", json={"url": "https://youtu.be/abcdefghijk"}).get_json()


def test_mine_an_expression_span_by_offset(client):
    data = load(client)
    span = data["lines"][0]["expressions"][0]
    assert span["text"] == SAYING
    response = client.post(
        "/api/mine",
        json={
            "line_index": 0,
            "target_line": 0,
            "target_text": span["text"],
            "target_offset": span["start"],
        },
    )
    assert response.status_code == 201
    card = response.get_json()
    assert card["target"] == SAYING
    assert card["parts"] == {"before": "他说", "target": SAYING, "after": "。"}
    assert card["target_kind"] == "suyu"
    assert card["target_entries"][0]["headword"] == SAYING


def test_wrong_offset_is_rejected(client):
    load(client)
    response = client.post(
        "/api/mine",
        json={"line_index": 0, "target_line": 0, "target_text": SAYING, "target_offset": 0},
    )
    assert response.status_code == 400


def test_fix_caption_updates_tokens_and_is_remembered(tmp_path, fixture_text):
    client = make_app(tmp_path, fixture_text)
    load(client)
    fixed = client.patch("/api/lines/1", json={"text": "我喜欢  一席话。"}).get_json()
    assert fixed["text"] == "我喜欢 一席话。"  # runs of spaces are collapsed
    assert fixed["edited"] is True
    assert "".join(t[0] for t in fixed["tokens"]) == fixed["text"]
    assert "一席话" in [t[0] for t in fixed["tokens"]]  # re-segmented with the dictionary

    # A fresh app (a restart) loading the same video gets the corrected text back.
    again = load(make_app(tmp_path, fixture_text))
    assert again["lines"][1]["text"] == "我喜欢 一席话。"
    assert again["lines"][1]["edited"] is True
    assert again["lines"][0]["edited"] is False


def test_mining_uses_the_fixed_text(client):
    load(client)
    client.patch("/api/lines/1", json={"text": "我喜欢一席话。"})
    card = client.post("/api/mine", json={"line_index": 1}).get_json()
    assert card["sentence"] == "我喜欢一席话。"
    assert card["pinyin"].startswith("wǒ xǐ huān")


def test_changing_a_caption_back_forgets_the_fix(tmp_path, fixture_text):
    client = make_app(tmp_path, fixture_text)
    load(client)
    client.patch("/api/lines/1", json={"text": "别的字"})
    restored = client.patch("/api/lines/1", json={"text": "我喜欢学习中文。"}).get_json()
    assert restored["edited"] is False
    assert load(make_app(tmp_path, fixture_text))["lines"][1]["edited"] is False


@pytest.mark.parametrize(
    "body", [{}, {"text": ""}, {"text": "   "}, {"text": 5}, {"text": "字" * 301}]
)
def test_bad_caption_fixes_are_rejected(client, body):
    load(client)
    assert client.patch("/api/lines/0", json=body).status_code == 400


def test_fix_needs_a_loaded_video_and_a_real_line(client):
    assert client.patch("/api/lines/0", json={"text": "x"}).status_code == 404
    load(client)
    assert client.patch("/api/lines/99", json={"text": "x"}).status_code == 404


def test_mined_words_come_from_cards_in_both_scripts(client):
    data = load(client)
    tokens = data["lines"][1]["tokens"]
    index = next(i for i, t in enumerate(tokens) if t[0] == "学习")
    client.post(
        "/api/mine",
        json={"line_index": 1, "target_index": index, "target_text": "学习"},
    )
    vocab = client.get("/api/vocab").get_json()
    assert {"学习", "學習"} <= set(vocab["mined"])
    assert vocab["known"] == []


def test_mark_word_known_and_unknown(client, tmp_path):
    vocab = client.post("/api/known", json={"word": "學習"}).get_json()
    assert {"學習", "学习"} <= set(vocab["known"])  # both spellings
    vocab = client.post("/api/known", json={"word": "学习", "known": False}).get_json()
    assert vocab["known"] == []
    assert client.post("/api/known", json={"word": ""}).status_code == 400
