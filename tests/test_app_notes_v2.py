"""What mining stores for the new note types, the settings routes, and sending through Anki."""

import json
import zipfile

import pytest

from test_ankiconnect import FakeAnki
from ytmine.app import Services, create_app
from ytmine.config import Settings
from ytmine.fetch import VideoData
from ytmine.lang import get_language
from ytmine.lang.cedict import build_database

ZH = """WEBVTT

00:00:02.000 --> 00:00:04.000
我喜欢学习中文。

00:00:05.000 --> 00:00:07.000
今天天气很好。
"""


@pytest.fixture
def anki():
    fake = FakeAnki()
    yield fake
    fake.close()


@pytest.fixture
def env(tmp_path, fixture_text, anki):
    audio = tmp_path / "audio.webm"
    audio.write_bytes(b"x")
    config_file = tmp_path / "config" / "config.json"
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        ankiconnect_url=anki.url,
        config_file=config_file,
    )
    build_database(fixture_text("cedict_mini.txt"), settings.cache_dir / "cedict" / "cedict.sqlite")
    services = Services(
        fetch_video=lambda url, cache: VideoData(
            "abcdefghijk", "Test video", 60.0, ZH, "manual (zh-Hans)", channel="Some Channel"
        ),
        download_audio=lambda vid, cache: audio,
        cut_clip=lambda ffmpeg, source, dest, start, length: dest.write_bytes(b"mp3"),
        find_ffmpeg=lambda: "ffmpeg",
    )
    client = create_app(settings, services).test_client()
    get_language("zh").wait_ready(10)
    return client, settings, config_file


def mine_word(client, word="学习", line=0):
    data = client.post("/api/load", json={"url": "https://youtu.be/abcdefghijk"}).get_json()
    tokens = data["lines"][line]["tokens"]
    index = next(i for i, t in enumerate(tokens) if t[0] == word)
    return client.post(
        "/api/mine",
        json={"line_index": line, "target_index": index, "target_text": word},
    ).get_json()


def test_mined_card_is_stamped_v2_with_channel_and_dictionary_snapshot(env):
    client, *_ = env
    card = mine_word(client)
    assert card["note_v"] == 2
    assert card["channel"] == "Some Channel"
    assert card["target_entries"][0]["definitions"] == ["to study; to learn"]
    assert card["target_kind"] == "word"


def test_editing_the_target_refreshes_the_snapshot(env):
    client, *_ = env
    card = mine_word(client)
    edited = client.patch(f"/api/cards/{card['id']}", json={"target": "中文"}).get_json()
    assert edited["target"] == "中文"
    assert edited["target_entries"] == []  # not in the fixture dictionary


def test_export_uses_the_new_note_types_and_the_saved_settings(env, tmp_path):
    client, *_ = env
    mine_word(client)
    client.put("/api/settings", json={"card_types": {"vocab_production": True}})
    response = client.get("/api/export.apkg")
    path = tmp_path / "got.apkg"
    path.write_bytes(response.data)
    with zipfile.ZipFile(path) as z:
        z.extract("collection.anki2", tmp_path)
    import sqlite3

    db = sqlite3.connect(tmp_path / "collection.anki2")
    names = json.loads(db.execute("select decks from col").fetchone()[0])
    assert {d["name"] for d in names.values()} >= {
        "ytmine Mandarin::Sentences",
        "ytmine Mandarin::Vocab",
    }
    # Listening, Reading, Recognition and Production.
    assert db.execute("select count(*) from cards").fetchone()[0] == 4
    db.close()


def test_settings_roundtrip_and_persist_to_config_json(env):
    client, settings, config_file = env
    defaults = client.get("/api/settings").get_json()
    assert defaults["card_types"] == {
        "listening": True,
        "reading": True,
        "vocab_recognition": True,
        "vocab_production": False,
    }
    assert defaults["deck_split"] == "none"
    saved = client.put(
        "/api/settings", json={"card_types": {"reading": False}, "deck_split": "channel"}
    ).get_json()
    assert saved["card_types"]["reading"] is False and saved["deck_split"] == "channel"
    on_disk = json.loads(config_file.read_text(encoding="utf-8"))
    assert on_disk["card_types"]["reading"] is False and on_disk["deck_split"] == "channel"


def test_saving_settings_keeps_other_keys_in_config_json(env):
    client, settings, config_file = env
    config_file.parent.mkdir(parents=True)
    config_file.write_text(json.dumps({"padding": 0.5, "ankiconnect_url": "x"}), encoding="utf-8")
    client.put("/api/settings", json={"deck_split": "video"})
    on_disk = json.loads(config_file.read_text(encoding="utf-8"))
    assert on_disk["padding"] == 0.5 and on_disk["deck_split"] == "video"


def test_config_json_is_read_back(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"deck_split": "channel", "card_types": {"vocab_production": True}}),
        encoding="utf-8",
    )
    settings = Settings(data_dir=tmp_path, cache_dir=tmp_path)
    settings._apply_config_file(config)
    assert settings.deck_split == "channel"
    assert settings.card_types["vocab_production"] is True
    assert settings.card_types["listening"] is True


@pytest.mark.parametrize(
    "body",
    [
        {"deck_split": "sideways"},
        {"card_types": {"nonsense": True}},
        {"card_types": {"reading": "yes"}},
        {"deck_name": ""},
        {"deck_name": "A::B"},
    ],
)
def test_bad_settings_are_rejected(env, body):
    client, *_ = env
    assert client.put("/api/settings", json=body).status_code == 400


def test_export_with_every_card_type_off_explains_itself(env):
    client, *_ = env
    mine_word(client)
    off = {k: False for k in ("listening", "reading", "vocab_recognition", "vocab_production")}
    client.put("/api/settings", json={"card_types": off})
    response = client.get("/api/export.apkg")
    assert response.status_code == 400
    assert "switched off" in response.get_json()["error"]


def test_send_to_anki_imports_the_same_package(env, anki):
    client, settings, _ = env
    mine_word(client)
    response = client.post("/api/anki/send", json={})
    assert response.status_code == 200
    assert response.get_json()["notes"] == 2  # one sentence note and one vocab note
    imported = next(c for c in anki.calls if c["action"] == "importPackage")
    path = imported["params"]["path"]
    assert path.endswith(".apkg")
    with zipfile.ZipFile(path) as z:  # the file Anki was told to read is a real package
        assert "collection.anki2" in z.namelist()
    assert not any(c["action"] in ("addNote", "createModel") for c in anki.calls)


def test_send_to_anki_when_anki_refuses_the_file(env, anki):
    client, *_ = env
    mine_word(client)
    anki.import_ok = False
    response = client.post("/api/anki/send", json={})
    assert response.status_code == 502
    assert "Download .apkg" in response.get_json()["error"]
