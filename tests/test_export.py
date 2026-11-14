import json
import sqlite3
import zipfile

import pytest

from ytmine.export import (
    DECK_ID,
    FIELDS,
    MODEL_ID,
    build_package,
    export_apkg,
    format_timestamp,
    front_html,
    note_fields,
)
from ytmine.lang import get_language


@pytest.fixture(scope="module")
def zh():
    return get_language("zh")


def make_card(**overrides):
    card = {
        "id": "abcdefghijk_12500",
        "lang": "zh",
        "video_id": "abcdefghijk",
        "start": 12.5,
        "end": 15.0,
        "sentence": "我喜欢学习中文",
        "pinyin": "wǒ xǐ huān xué xí zhōng wén",
        "english": "I like studying Chinese <3",
        "target": "学习",
        "target_start": 3,
        "audio_file": "ytmine_abcdefghijk_12500.mp3",
    }
    card.update(overrides)
    return card


def read_apkg(path, workdir):
    """Return (notes, media map, member names, decks, models) from an .apkg file."""
    workdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as z:
        z.extract("collection.anki2", workdir)
        media = json.loads(z.read("media"))
        names = z.namelist()
    db = sqlite3.connect(workdir / "collection.anki2")
    notes = db.execute("select guid, flds from notes order by id").fetchall()
    decks = json.loads(db.execute("select decks from col").fetchone()[0])
    models = json.loads(db.execute("select models from col").fetchone()[0])
    db.close()
    return notes, media, names, decks, models


def test_format_timestamp():
    assert format_timestamp(5) == "0:05"
    assert format_timestamp(125.9) == "2:05"
    assert format_timestamp(3725) == "1:02:05"


def test_front_blanks_the_target(zh):
    assert front_html(make_card(), zh) == '我喜欢<span class="blank">[...]</span>中文'


def test_front_without_target_shows_whole_sentence(zh):
    assert front_html(make_card(target="", target_start=None), zh) == "我喜欢学习中文"


def test_front_escapes_html(zh):
    card = make_card(sentence="a<b>c", target="", target_start=None)
    assert front_html(card, zh) == "a&lt;b&gt;c"


def test_note_fields_back_side_content(zh):
    fields = note_fields(make_card(), zh)
    assert fields["Audio"] == "[sound:ytmine_abcdefghijk_12500.mp3]"
    assert fields["Sentence"] == "我喜欢学习中文"
    assert fields["English"] == "I like studying Chinese &lt;3"
    assert "https://www.youtube.com/watch?v=abcdefghijk&amp;t=12s" in fields["Link"]
    assert ">0:12<" in fields["Link"]


def test_export_writes_notes_media_and_fixed_ids(tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    (media_dir / "ytmine_abcdefghijk_12500.mp3").write_bytes(b"fake mp3")
    out = export_apkg([make_card()], media_dir, "Test deck", tmp_path / "out" / "deck.apkg")

    notes, media, names, decks, models = read_apkg(out, tmp_path / "read")
    assert len(notes) == 1
    fields = notes[0][1].split("\x1f")
    assert len(fields) == len(FIELDS)
    assert fields[0] == '我喜欢<span class="blank">[...]</span>中文'
    assert list(media.values()) == ["ytmine_abcdefghijk_12500.mp3"]
    assert "0" in names
    assert decks[str(DECK_ID)]["name"] == "Test deck"
    assert str(MODEL_ID) in models


def test_reexport_keeps_ids_and_guids(tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    first = export_apkg([make_card()], media_dir, "Deck", tmp_path / "a.apkg")
    second = export_apkg(
        [make_card(english="edited translation")], media_dir, "Deck", tmp_path / "b.apkg"
    )
    notes_a, *_ = read_apkg(first, tmp_path / "x")
    notes_b, *_ = read_apkg(second, tmp_path / "y")
    assert notes_a[0][0] == notes_b[0][0]  # same GUID, so Anki updates the note in place
    assert notes_a[0][1] != notes_b[0][1]


def test_missing_audio_file_exports_text_only(tmp_path):
    package = build_package([make_card()], tmp_path, "Deck")
    assert package.media_files == []
    note = package.decks[0].notes[0]
    assert note.fields[FIELDS.index("Audio")] == ""


def test_apostrophes_are_not_escaped(zh):
    fields = note_fields(make_card(english='it\'s "fine" <ok>'), zh)
    assert fields["English"] == 'it\'s "fine" &lt;ok&gt;'


def test_note_mod_time_is_the_card_edit_time_not_the_export_time(tmp_path):
    """Otherwise Anki's "update if newer" import overwrites edits made in Anki."""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    out = export_apkg(
        [make_card(modified_at=1_700_000_000.0)], media_dir, "Deck", tmp_path / "a.apkg"
    )
    with zipfile.ZipFile(out) as z:
        z.extract("collection.anki2", tmp_path)
    db = sqlite3.connect(tmp_path / "collection.anki2")
    assert db.execute("select mod from notes").fetchone()[0] == 1_700_000_000
    db.close()


def test_card_modified_falls_back_to_created_at():
    import time

    from ytmine.export import card_modified

    card = make_card(created_at="2026-01-02T03:04:05")
    assert card_modified(card) == time.mktime(
        time.strptime("2026-01-02T03:04:05", "%Y-%m-%dT%H:%M:%S")
    )
