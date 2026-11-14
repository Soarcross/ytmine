"""The v2 sentence and vocab note types: decks, tags, toggles, vocab dedupe and legacy cards."""

import json
import sqlite3
import zipfile

import pytest

from ytmine import export
from ytmine.export import (
    DECK_ID,
    MODEL_ID,
    SENTENCE_V2_FIELDS,
    SENTENCE_V2_MODEL_ID,
    SENTENCES_DECK_ID,
    VOCAB_DECK_ID,
    VOCAB_FIELDS,
    VOCAB_MODEL_ID,
    ExportOptions,
    build_package,
    export_apkg,
    note_count,
)
from ytmine.lang import get_language

ENTRY = {
    "headword": "一举两得",
    "alt_form": "一舉兩得",
    "reading": "yī jǔ liǎng dé",
    "definitions": ["one action, two gains (idiom)", "two birds with one stone"],
    "kind": "chengyu",
}


def v2_card(n=1, **overrides):
    card = {
        "id": f"vid{n:08d}x_{n}000",
        "note_v": 2,
        "lang": "zh",
        "video_id": f"vid{n:08d}x",
        "title": "A video",
        "channel": "My Channel",
        "start": float(n),
        "end": n + 2.0,
        "sentence": "这样做一举两得",
        "pinyin": "zhè yàng zuò yī jǔ liǎng dé",
        "english": "This kills two birds with one stone",
        "target": "一举两得",
        "target_start": 3,
        "target_entries": [ENTRY],
        "target_kind": "chengyu",
        "audio_file": f"clip{n}.mp3",
        "created_at": f"2026-01-0{n}T10:00:00",
    }
    card.update(overrides)
    return card


def read_package(package, workdir):
    """Write the package, then read back notes, cards, decks, models and media."""
    path = workdir / "p.apkg"
    workdir.mkdir(parents=True, exist_ok=True)
    package.write_to_file(str(path))
    with zipfile.ZipFile(path) as z:
        z.extract("collection.anki2", workdir)
        media = json.loads(z.read("media"))
    db = sqlite3.connect(workdir / "collection.anki2")
    notes = db.execute("select guid, mid, flds, tags, id from notes order by id").fetchall()
    cards = db.execute("select nid, ord, did from cards order by id").fetchall()
    decks = json.loads(db.execute("select decks from col").fetchone()[0])
    models = json.loads(db.execute("select models from col").fetchone()[0])
    db.close()
    return notes, cards, decks, models, media


@pytest.fixture
def media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    for n in range(1, 4):
        (folder / f"clip{n}.mp3").write_bytes(b"mp3")
    return folder


def fields_of(note):
    return note[2].split("\x1f")


def notes_of_model(notes, model_id):
    return [n for n in notes if n[1] == model_id]


def named(names, note):
    return dict(zip(names, fields_of(note), strict=True))


def test_new_card_makes_a_sentence_note_and_a_vocab_note(tmp_path, media):
    pkg = build_package([v2_card()], media, "Deck")
    notes, cards, _, models, _ = read_package(pkg, tmp_path / "r")
    assert {SENTENCE_V2_MODEL_ID, VOCAB_MODEL_ID} <= {int(m) for m in models}
    assert len({MODEL_ID, SENTENCE_V2_MODEL_ID, VOCAB_MODEL_ID}) == 3
    assert len(notes_of_model(notes, SENTENCE_V2_MODEL_ID)) == 1
    assert len(notes_of_model(notes, VOCAB_MODEL_ID)) == 1
    # Listening and Reading for the sentence, Recognition for the vocab word.
    assert len(cards) == 3


def test_deck_tree_has_sentences_and_vocab_subdecks_with_fixed_ids(tmp_path, media):
    _, _, decks, _, _ = read_package(build_package([v2_card()], media, "Deck"), tmp_path / "r")
    assert decks[str(SENTENCES_DECK_ID)]["name"] == "Deck::Sentences"
    assert decks[str(VOCAB_DECK_ID)]["name"] == "Deck::Vocab"


def test_cards_land_in_the_right_decks(tmp_path, media):
    notes, cards, *_ = read_package(build_package([v2_card()], media, "Deck"), tmp_path / "r")
    model_of = {n[4]: n[1] for n in notes}
    for nid, _ord, did in cards:
        expected = SENTENCES_DECK_ID if model_of[nid] == SENTENCE_V2_MODEL_ID else VOCAB_DECK_ID
        assert did == expected


def test_tags(tmp_path, media):
    notes, *_ = read_package(build_package([v2_card()], media, "Deck"), tmp_path / "r")
    for note in notes:
        tags = note[3].split()
        assert "ytmine" in tags
        assert "ytmine::video::vid00000001x" in tags
        assert "ytmine::channel::my_channel" in tags
        assert "ytmine::kind::chengyu" in tags


def test_plain_word_gets_no_kind_tag(tmp_path, media):
    plain = {**ENTRY, "headword": "学习", "kind": "word"}
    card = v2_card(
        target="学习", target_entries=[plain], target_kind="word", sentence="我学习", target_start=1
    )
    notes, *_ = read_package(build_package([card], media, "Deck"), tmp_path / "r")
    assert all("ytmine::kind" not in n[3] for n in notes)


def test_vocab_note_fields(tmp_path, media):
    notes, *_ = read_package(build_package([v2_card()], media, "Deck"), tmp_path / "r")
    vocab = named(VOCAB_FIELDS, notes_of_model(notes, VOCAB_MODEL_ID)[0])
    assert vocab["Word"] == "一举两得"
    assert vocab["Traditional"] == "一舉兩得"
    assert vocab["Pinyin"] == "yī jǔ liǎng dé"
    assert "two birds with one stone" in vocab["Definition"]
    assert vocab["Kind"].startswith("成语")
    assert '<span class="hl">一举两得</span>' in vocab["SentenceWithWordHighlighted"]
    assert vocab["SentenceAudio"] == "[sound:clip1.mp3]"
    assert vocab["SourceId"] == "vid00000001x_1000"
    assert vocab["RecognitionCard"] == "y"
    assert vocab["ProductionCard"] == ""


def test_sentence_note_fields(tmp_path, media):
    notes, *_ = read_package(build_package([v2_card()], media, "Deck"), tmp_path / "r")
    sentence = named(SENTENCE_V2_FIELDS, notes_of_model(notes, SENTENCE_V2_MODEL_ID)[0])
    assert sentence["Blanked"] == '这样做<span class="blank">[...]</span>'
    assert sentence["Highlighted"] == '这样做<span class="hl">一举两得</span>'
    assert "two birds" in sentence["Definition"]
    assert (sentence["ListeningCard"], sentence["ReadingCard"]) == ("y", "y")


def test_vocab_is_deduplicated_across_videos_and_adds_extra_examples(tmp_path, media):
    first = v2_card(1)
    second = v2_card(2, sentence="真是一举两得啊", target_start=2, english="Second example")
    notes, *_ = read_package(build_package([first, second], media, "Deck"), tmp_path / "r")
    assert len(notes_of_model(notes, SENTENCE_V2_MODEL_ID)) == 2  # one per sentence
    vocab_notes = notes_of_model(notes, VOCAB_MODEL_ID)
    assert len(vocab_notes) == 1  # one per headword
    vocab = named(VOCAB_FIELDS, vocab_notes[0])
    assert vocab["SourceId"] == first["id"]  # the first card stays the main example
    assert "Second example" in vocab["Examples"]
    assert "[sound:clip2.mp3]" in vocab["Examples"]
    for tag in ("ytmine::video::vid00000001x", "ytmine::video::vid00000002x"):
        assert tag in vocab_notes[0][3]


def test_vocab_guid_is_stable_when_more_sentences_are_mined(tmp_path, media):
    a, *_ = read_package(build_package([v2_card(1)], media, "Deck"), tmp_path / "a")
    b, *_ = read_package(build_package([v2_card(1), v2_card(2)], media, "Deck"), tmp_path / "b")

    def vocab_guid(notes):
        return next(n[0] for n in notes if n[1] == VOCAB_MODEL_ID)

    assert vocab_guid(a) == vocab_guid(b)


def test_traditional_and_simplified_mining_share_one_vocab_note(tmp_path, media):
    simp = v2_card(1)
    trad = v2_card(2, sentence="這樣做一舉兩得", target="一舉兩得", target_start=3)
    notes, *_ = read_package(build_package([simp, trad], media, "Deck"), tmp_path / "r")
    assert len(notes_of_model(notes, VOCAB_MODEL_ID)) == 1


def test_card_toggles_control_which_cards_exist(tmp_path, media):
    options = ExportOptions(
        listening=False, reading=True, vocab_recognition=False, vocab_production=True
    )
    pkg = build_package([v2_card()], media, "Deck", options)
    notes, cards, *_ = read_package(pkg, tmp_path / "r")
    model_of = {n[4]: n[1] for n in notes}
    made = sorted((model_of[nid], ordinal) for nid, ordinal, _ in cards)
    # Sentence: only Reading (template 1). Vocab: only Production (template 1).
    assert made == sorted([(SENTENCE_V2_MODEL_ID, 1), (VOCAB_MODEL_ID, 1)])


def test_all_sentence_types_off_skips_sentence_notes(tmp_path, media):
    options = ExportOptions(listening=False, reading=False)
    notes, *_ = read_package(build_package([v2_card()], media, "Deck", options), tmp_path / "r")
    assert [n[1] for n in notes] == [VOCAB_MODEL_ID]


def test_everything_off_makes_an_empty_package(media):
    options = ExportOptions(
        listening=False, reading=False, vocab_recognition=False, vocab_production=False
    )
    assert note_count(build_package([v2_card()], media, "Deck", options)) == 0


def test_no_target_means_no_vocab_note(tmp_path, media):
    card = v2_card(target="", target_start=None, target_entries=[], target_kind="")
    notes, *_ = read_package(build_package([card], media, "Deck"), tmp_path / "r")
    assert [n[1] for n in notes] == [SENTENCE_V2_MODEL_ID]


def test_split_by_channel_and_video(tmp_path, media):
    cards = [v2_card(1), v2_card(2, channel="Other: Channel")]
    pkg = build_package(cards, media, "Deck", ExportOptions(deck_split="channel"))
    _, _, decks, _, _ = read_package(pkg, tmp_path / "a")
    names = {d["name"] for d in decks.values()}
    assert "Deck::Sentences::My Channel" in names
    assert "Deck::Sentences::Other- Channel" in names  # no "::" inside a level
    pkg = build_package(cards, media, "Deck", ExportOptions(deck_split="video"))
    _, _, decks, _, _ = read_package(pkg, tmp_path / "b")
    assert "Deck::Sentences::A video" in {d["name"] for d in decks.values()}


def test_split_ids_are_stable():
    assert export._deck_id("channel", "My Channel") == export._deck_id("channel", "My Channel")
    assert export._deck_id("channel", "a") != export._deck_id("channel", "b")


def test_templates_use_toggle_fields_and_night_mode_css():
    reading = export.SENTENCE_TEMPLATES[1]["qfmt"]
    assert "{{#ReadingCard}}" in reading and "{{Highlighted}}" in reading
    assert "{{Audio}}" not in reading  # the reading front has no audio
    assert "{{Audio}}" in export.SENTENCE_TEMPLATES[0]["qfmt"]
    assert "{{#ProductionCard}}" in export.VOCAB_TEMPLATES[1]["qfmt"]
    assert ".nightMode" in export.CSS_V2


def test_old_cards_still_export_through_the_original_note_type(tmp_path, media):
    legacy = {k: v for k, v in v2_card().items() if k not in ("note_v", "target_entries")}
    notes, cards, decks, _, _ = read_package(build_package([legacy], media, "Deck"), tmp_path / "r")
    assert [n[1] for n in notes] == [MODEL_ID]
    assert decks[str(DECK_ID)]["name"] == "Deck"
    assert len(cards) == 1
    fields = fields_of(notes[0])
    assert fields[0] == '这样做<span class="blank">[...]</span>'
    assert len(fields) == len(export.FIELDS)
    assert notes[0][3].split() == ["ytmine"]


def test_old_and_new_cards_in_one_export(tmp_path, media):
    legacy = {k: v for k, v in v2_card(1).items() if k != "note_v"}
    notes, *_ = read_package(build_package([legacy, v2_card(2)], media, "Deck"), tmp_path / "r")
    assert {n[1] for n in notes} == {MODEL_ID, SENTENCE_V2_MODEL_ID, VOCAB_MODEL_ID}


def test_missing_audio_is_dropped_from_every_field(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    pkg = build_package([v2_card(1), v2_card(2)], empty, "Deck")
    assert pkg.media_files == []
    for deck in pkg.decks:
        for note in deck.notes:
            assert not any("[sound:" in f for f in note.fields)


def test_entries_are_looked_up_at_export_when_none_were_saved(monkeypatch):
    zh = get_language("zh")
    monkeypatch.setattr(zh, "lookup", lambda word: [export_entry()])
    card = v2_card(target_entries=[], target_kind="")
    fields = export.vocab_fields([card], zh, ExportOptions())
    assert "late lookup" in fields["Definition"]


def export_entry():
    from ytmine.lang.base import Entry

    return Entry("一举两得", "", "yī jǔ liǎng dé", ("late lookup",), "chengyu")


def test_export_apkg_writes_a_file(tmp_path, media):
    out = export_apkg([v2_card()], media, "Deck", tmp_path / "o" / "d.apkg")
    with zipfile.ZipFile(out) as z:
        assert json.loads(z.read("media")) == {"0": "clip1.mp3"}


def test_vocab_definition_drops_the_repeated_reading_unless_there_are_several(tmp_path, media):
    notes, *_ = read_package(build_package([v2_card()], media, "Deck"), tmp_path / "r")
    vocab = named(VOCAB_FIELDS, notes_of_model(notes, VOCAB_MODEL_ID)[0])
    assert 'class="rd"' not in vocab["Definition"]
    two = [
        {**ENTRY, "reading": "xíng", "definitions": ["to walk"]},
        {**ENTRY, "reading": "háng", "definitions": ["row"]},
    ]
    notes, *_ = read_package(
        build_package([v2_card(target_entries=two)], media, "Deck"), tmp_path / "s"
    )
    vocab = named(VOCAB_FIELDS, notes_of_model(notes, VOCAB_MODEL_ID)[0])
    assert vocab["Definition"].count('class="rd"') == 2
