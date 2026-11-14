"""Build Anki decks (.apkg) from mined cards.

The deck and note type IDs below are fixed on purpose. Anki matches imports by
ID, so re-importing a newer export updates the same deck and notes instead of
creating copies. Each note's GUID comes from the card id (video id + start
time), so the same line always maps to the same note.

The note type is shared with the AnkiConnect sender (see ankiconnect.py).
"""

from __future__ import annotations

import html
import time
from pathlib import Path

import genanki

from ytmine.fetch import watch_url
from ytmine.lang import Language, get_language

# Never change these once released, or existing users will get duplicate decks.
MODEL_ID = 1812734606
DECK_ID = 2040316857

MODEL_NAME = "ytmine Mandarin sentence"
FIELDS = ["Front", "Audio", "Sentence", "Target", "Pinyin", "English", "Link"]
CARD_NAME = "Sentence"
FRONT_TEMPLATE = '<div class="sentence">{{Front}}</div>\n{{Audio}}'
BACK_TEMPLATE = (
    '<div class="sentence">{{Sentence}}</div>\n'
    '{{#Target}}<div class="target">{{Target}}</div>{{/Target}}\n'
    '<div class="pinyin">{{Pinyin}}</div>\n'
    '<div class="english">{{English}}</div>\n'
    '<div class="link">{{Link}}</div>'
)
CSS = """
.card { font-family: "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
        text-align: center; font-size: 20px; }
.sentence { font-size: 30px; margin: 12px 0; }
.blank { color: #3b82f6; font-weight: bold; }
.target { font-size: 22px; color: #3b82f6; }
.pinyin { color: #888; }
.english { margin-top: 8px; }
.link { margin-top: 12px; font-size: 14px; }
"""


def _text(value: str) -> str:
    """Escape text for an Anki field. Quotes stay as-is so searches like "it's" still match."""
    return html.escape(value, quote=False)


def card_modified(card: dict) -> float:
    """When the card last changed in ytmine (seconds since the epoch).

    Written as the note's modification time, so Anki's "update if newer" import
    keeps edits made in Anki after the card was mined or last edited here.
    """
    if isinstance(card.get("modified_at"), (int, float)):
        return float(card["modified_at"])
    try:
        return time.mktime(time.strptime(card.get("created_at", ""), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return time.time()


class _Note(genanki.Note):
    """A genanki note that keeps its own modification time instead of the export time."""

    def __init__(self, *args, modified: float, **kwargs):
        super().__init__(*args, **kwargs)
        self.modified = modified

    def write_to_db(self, cursor, timestamp, deck_id, id_gen):
        super().write_to_db(cursor, self.modified, deck_id, id_gen)


def format_timestamp(seconds: float) -> str:
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def front_html(card: dict, lang: Language) -> str:
    """The sentence with the target word replaced by a blank (if a target is set)."""
    sentence = card["sentence"]
    parts = lang.cloze(sentence, card.get("target") or "", card.get("target_start"))
    if parts is None:
        return _text(sentence)
    blank = f'<span class="blank">{_text(lang.blank)}</span>'
    return _text(parts.before) + blank + _text(parts.after)


def note_fields(card: dict, lang: Language) -> dict[str, str]:
    """Field name -> value for one card. Used by both the .apkg export and AnkiConnect."""
    audio = card.get("audio_file")
    link = watch_url(card["video_id"], card["start"])
    return {
        "Front": front_html(card, lang),
        "Audio": f"[sound:{audio}]" if audio else "",
        "Sentence": _text(card["sentence"]),
        "Target": _text(card.get("target") or ""),
        "Pinyin": _text(card.get("pinyin") or ""),
        "English": _text(card.get("english") or ""),
        "Link": f'<a href="{html.escape(link)}">{format_timestamp(card["start"])}</a>',
    }


def _model() -> genanki.Model:
    return genanki.Model(
        MODEL_ID,
        MODEL_NAME,
        fields=[{"name": name} for name in FIELDS],
        templates=[{"name": CARD_NAME, "qfmt": FRONT_TEMPLATE, "afmt": BACK_TEMPLATE}],
        css=CSS,
    )


def build_package(
    cards: list[dict], media_dir: Path, deck_name: str, lang: Language | None = None
) -> genanki.Package:
    """Assemble a genanki package with every card's note and audio clip."""
    model = _model()
    deck = genanki.Deck(DECK_ID, deck_name)
    media_files: list[str] = []
    for card in cards:
        card_lang = lang or get_language(card.get("lang", "zh"))
        fields = note_fields(card, card_lang)
        audio_path = media_dir / card["audio_file"] if card.get("audio_file") else None
        if audio_path is not None and audio_path.is_file():
            media_files.append(str(audio_path))
        else:
            fields["Audio"] = ""  # clip file is gone; export the text anyway
        deck.add_note(
            _Note(
                model=model,
                fields=[fields[name] for name in FIELDS],
                guid=genanki.guid_for(card["id"]),
                tags=["ytmine"],
                modified=card_modified(card),
            )
        )
    package = genanki.Package(deck)
    package.media_files = media_files
    return package


def export_apkg(cards: list[dict], media_dir: Path, deck_name: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build_package(cards, media_dir, deck_name).write_to_file(str(out_path))
    return out_path
