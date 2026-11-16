"""Build Anki decks (.apkg) from mined cards.

The deck and note type IDs below are fixed on purpose. Anki matches imports by
ID, so re-importing a newer export updates the same deck and notes instead of
creating copies. Each note's GUID comes from the card id (video id + start
time), so the same line always maps to the same note.

There are three note types. The original "ytmine Mandarin sentence" type (MODEL_ID) is
frozen: cards mined before the rework keep exporting through it unchanged. New cards
(`note_v` == 2) use the sentence v2 and vocab types below, each with its own fixed ID.
"Send to Anki" imports this same .apkg (see ankiconnect.py), so both routes share them.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
from dataclasses import dataclass
from pathlib import Path

import genanki

from ytmine.fetch import watch_url
from ytmine.history import sentence_key
from ytmine.lang import Language, get_language

# Never change these once released, or existing users will get duplicate decks.
MODEL_ID = 1812734606
DECK_ID = 2040316857

# Added in the rework. Also permanent.
SENTENCE_V2_MODEL_ID = 1735428291
VOCAB_MODEL_ID = 1926035417
SENTENCES_DECK_ID = 1618204475
VOCAB_DECK_ID = 2074981536

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


# -- v2: sentence and vocab note types ------------------------------------------------

SENTENCE_V2_NAME = "ytmine Mandarin sentence v2"
SENTENCE_V2_FIELDS = [
    "Sentence", "Audio", "Blanked", "Highlighted", "Target", "Pinyin", "English",
    "Definition", "Link", "ListeningCard", "ReadingCard",
]  # fmt: skip
VOCAB_NAME = "ytmine Mandarin vocab"
VOCAB_FIELDS = [
    "Word", "Traditional", "Pinyin", "Definition", "Kind", "SentenceWithWordHighlighted",
    "SentenceBlanked", "SentenceAudio", "English", "Link", "Examples", "SourceId",
    "RecognitionCard", "ProductionCard",
]  # fmt: skip

_SENTENCE_DETAILS = (
    '<div class="sentence">{{Sentence}}</div>\n'
    '{{#Target}}<div class="target">{{Target}}</div>{{/Target}}\n'
    '<div class="pinyin">{{Pinyin}}</div>\n'
    '{{#Definition}}<div class="definition">{{Definition}}</div>{{/Definition}}\n'
    '<div class="english">{{English}}</div>\n'
    '<div class="link">{{Link}}</div>'
)
# A card is only made when its front renders something, so a card type is switched off by
# leaving its toggle field empty (Anki's own rule for conditional fronts).
SENTENCE_TEMPLATES = [
    {
        "name": "Listening",
        "qfmt": (
            '{{#ListeningCard}}<div class="wrap">{{Audio}}'
            '<div class="sentence">{{Blanked}}</div></div>{{/ListeningCard}}'
        ),
        "afmt": '{{FrontSide}}<hr id="answer"><div class="wrap">' + _SENTENCE_DETAILS + "</div>",
    },
    {
        "name": "Reading",
        "qfmt": (
            '{{#ReadingCard}}<div class="wrap">'
            '<div class="sentence">{{Highlighted}}</div></div>{{/ReadingCard}}'
        ),
        "afmt": (
            '{{FrontSide}}<hr id="answer"><div class="wrap">'
            + _SENTENCE_DETAILS
            + "{{Audio}}</div>"
        ),
    },
]
_VOCAB_SENTENCE = (
    '<div class="sentence">{{SentenceWithWordHighlighted}}</div>{{SentenceAudio}}\n'
    '<div class="english">{{English}}</div>\n'
    '{{#Examples}}<div class="examples">{{Examples}}</div>{{/Examples}}\n'
    '<div class="link">{{Link}}</div>'
)
VOCAB_TEMPLATES = [
    {
        "name": "Recognition",
        "qfmt": (
            '{{#RecognitionCard}}<div class="wrap"><div class="word">{{Word}}</div></div>'
            "{{/RecognitionCard}}"
        ),
        "afmt": (
            '{{FrontSide}}<hr id="answer"><div class="wrap">'
            '{{#Traditional}}<div class="trad">{{Traditional}}</div>{{/Traditional}}\n'
            '<div class="pinyin">{{Pinyin}}</div>\n'
            '{{#Kind}}<div class="kind">{{Kind}}</div>{{/Kind}}\n'
            '<div class="definition">{{Definition}}</div>\n' + _VOCAB_SENTENCE + "</div>"
        ),
    },
    {
        "name": "Production",
        "qfmt": (
            '{{#ProductionCard}}<div class="wrap"><div class="definition">{{Definition}}</div>'
            '<div class="sentence">{{SentenceBlanked}}</div></div>{{/ProductionCard}}'
        ),
        "afmt": (
            '{{FrontSide}}<hr id="answer"><div class="wrap"><div class="word">{{Word}}</div>\n'
            '<div class="pinyin">{{Pinyin}}</div>\n' + _VOCAB_SENTENCE + "</div>"
        ),
    },
]
CSS_V2 = """
.card { font-family: "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 20px; line-height: 1.5; text-align: center; color: #1f2328;
        background: #ffffff; margin: 0; padding: 8px; }
.wrap { max-width: 640px; margin: 0 auto; }
.sentence { font-size: 30px; margin: 12px 0; }
.word { font-size: 44px; margin: 16px 0 8px; }
.trad { font-size: 22px; color: #6b7280; }
.blank, .hl, .target { color: #2563eb; font-weight: bold; }
.target { font-size: 22px; }
.pinyin { color: #6b7280; }
.kind { display: inline-block; font-size: 14px; color: #6b7280; border: 1px solid #9ca3af;
        border-radius: 10px; padding: 0 8px; margin-top: 4px; }
.definition { margin: 8px 0; text-align: left; display: inline-block; max-width: 100%; }
.definition .rd { color: #6b7280; margin-right: 6px; }
.english { margin-top: 8px; }
.examples { margin-top: 12px; font-size: 16px; text-align: left; }
.examples .ex { margin: 6px 0; }
.link { margin-top: 12px; font-size: 14px; }
hr#answer { border: 0; border-top: 1px solid #d1d5db; margin: 16px 0; }
.nightMode .card, .card.nightMode { color: #e5e7eb; background: #1e1e1e; }
.nightMode .blank, .nightMode .hl, .nightMode .target,
.card.nightMode .blank, .card.nightMode .hl, .card.nightMode .target { color: #60a5fa; }
.nightMode .pinyin, .nightMode .trad, .nightMode .kind, .nightMode .definition .rd,
.card.nightMode .pinyin, .card.nightMode .trad, .card.nightMode .kind,
.card.nightMode .definition .rd { color: #9ca3af; }
.nightMode hr#answer, .card.nightMode hr#answer { border-top-color: #4b5563; }
.nightMode a, .card.nightMode a { color: #93c5fd; }
@media (max-width: 480px) {
  .sentence { font-size: 24px; }
  .word { font-size: 36px; }
  .card { font-size: 18px; }
}
"""

KIND_LABELS = {"chengyu": "成语 chengyu", "suyu": "俗语 suyu", "xiehouyu": "歇后语 xiehouyu"}
MAX_EXTRA_EXAMPLES = 5


@dataclass
class ExportOptions:
    """Which cards to build and how to lay out the decks (from the web UI's settings)."""

    listening: bool = True
    reading: bool = True
    vocab_recognition: bool = True
    vocab_production: bool = False
    deck_split: str = "none"  # none, channel or video

    @property
    def sentences(self) -> bool:
        return self.listening or self.reading

    @property
    def vocab(self) -> bool:
        return self.vocab_recognition or self.vocab_production

    @classmethod
    def from_settings(cls, settings) -> ExportOptions:
        types = settings.card_types
        return cls(
            listening=types.get("listening", True),
            reading=types.get("reading", True),
            vocab_recognition=types.get("vocab_recognition", True),
            vocab_production=types.get("vocab_production", False),
            deck_split=settings.deck_split,
        )


def is_v2(card: dict) -> bool:
    """New cards are stamped note_v 2 when mined. Older cards stay on the original note type."""
    return card.get("note_v") == 2


def slug(name: str) -> str:
    """A tag-safe version of a name: no spaces, colons or punctuation. Chinese is kept."""
    return re.sub(r"[^\w-]+", "_", name.strip().lower()).strip("_")


def _deck_id(kind: str, key: str) -> int:
    """A stable deck ID for a per-channel or per-video subdeck."""
    digest = hashlib.sha1(f"ytmine:{kind}:{key}".encode()).hexdigest()
    return 100_000_000 + int(digest[:8], 16) % 1_900_000_000


def _deck_part(name: str) -> str:
    """One level of a deck name. Anki uses '::' as the separator, so it cannot appear inside."""
    return re.sub(r"\s+", " ", name.replace(":", "-")).strip()[:60] or "Unknown"


def card_entries(card: dict, lang: Language) -> list[dict]:
    """Dictionary entries for the card's target word, as saved when it was mined.

    If none were saved (the dictionary was offline then), look them up now.
    """
    target = card.get("target") or ""
    if not target:
        return []
    saved = card.get("target_entries")
    if saved:
        return saved
    return [e.to_dict() for e in lang.lookup(target)][:4]


def card_kind(card: dict, entries: list[dict]) -> str:
    kinds = {e.get("kind") for e in entries} | {card.get("target_kind")}
    for kind in ("chengyu", "suyu", "xiehouyu"):
        if kind in kinds:
            return kind
    return "word"


def definition_html(entries: list[dict], always_show_reading: bool = True) -> str:
    """Definitions grouped by reading, so a 多音字 shows each pronunciation with its meanings.

    With one reading the label is dropped when the card already shows that pinyin elsewhere.
    """
    shown = entries[:4]
    label = always_show_reading or len({e["reading"] for e in shown}) > 1
    rows = []
    for entry in shown:
        defs = "; ".join(entry.get("definitions", [])[:6])
        reading = f'<span class="rd">{_text(entry["reading"])}</span>' if label else ""
        rows.append(f'<div class="def">{reading}{_text(defs)}</div>')
    return "".join(rows)


def highlighted_html(card: dict, lang: Language) -> str:
    """The sentence with the target word marked (no blank)."""
    sentence = card["sentence"]
    parts = lang.cloze(sentence, card.get("target") or "", card.get("target_start"))
    if parts is None:
        return _text(sentence)
    mark = f'<span class="hl">{_text(parts.target)}</span>'
    return _text(parts.before) + mark + _text(parts.after)


def card_tags(card: dict, kind: str) -> list[str]:
    tags = ["ytmine", f"ytmine::video::{card['video_id']}"]
    channel = slug(card.get("channel") or "")
    if channel:
        tags.append(f"ytmine::channel::{channel}")
    if kind in KIND_LABELS:
        tags.append(f"ytmine::kind::{kind}")
    return tags


def _toggle(on: bool) -> str:
    return "y" if on else ""


def sentence_fields_v2(card: dict, lang: Language, options: ExportOptions) -> dict[str, str]:
    entries = card_entries(card, lang)
    audio = card.get("audio_file")
    link = watch_url(card["video_id"], card["start"])
    return {
        "Sentence": _text(card["sentence"]),
        "Audio": f"[sound:{audio}]" if audio else "",
        "Blanked": front_html(card, lang),
        "Highlighted": highlighted_html(card, lang),
        "Target": _text(card.get("target") or ""),
        "Pinyin": _text(card.get("pinyin") or ""),
        "English": _text(card.get("english") or ""),
        "Definition": definition_html(entries),
        "Link": f'<a href="{html.escape(link)}">{format_timestamp(card["start"])}</a>',
        "ListeningCard": _toggle(options.listening),
        "ReadingCard": _toggle(options.reading),
    }


def vocab_key(card: dict, lang: Language) -> str:
    """One vocab note per headword. Simplified and traditional spellings share a key."""
    entries = card_entries(card, lang)
    return sentence_key(entries[0]["headword"] if entries else card["target"])


def vocab_fields(group: list[dict], lang: Language, options: ExportOptions) -> dict[str, str]:
    """Fields for one vocab note. `group` is every card that mined this word, oldest first.

    The oldest card supplies the main example. The others become extra examples, so mining
    a word again from another video adds to the note instead of creating a second one.
    """
    first = group[0]
    entries = next((e for c in group if (e := card_entries(c, lang))), [])
    kind = card_kind(first, entries)
    audio = first.get("audio_file")
    link = watch_url(first["video_id"], first["start"])
    readings = list(dict.fromkeys(e["reading"] for e in entries))
    examples = "".join(
        f'<div class="ex">{highlighted_html(c, lang)} '
        f'<span class="en">{_text(c.get("english") or "")}</span>'
        f"{'[sound:' + c['audio_file'] + ']' if c.get('audio_file') else ''}</div>"
        for c in group[1 : 1 + MAX_EXTRA_EXAMPLES]
    )
    return {
        "Word": _text(entries[0]["headword"] if entries else first["target"]),
        "Traditional": _text(next((e["alt_form"] for e in entries if e["alt_form"]), "")),
        "Pinyin": _text(" / ".join(readings) or lang.reading_text(first["target"])),
        "Definition": definition_html(entries, always_show_reading=False),
        "Kind": KIND_LABELS.get(kind, ""),
        "SentenceWithWordHighlighted": highlighted_html(first, lang),
        "SentenceBlanked": front_html(first, lang),
        "SentenceAudio": f"[sound:{audio}]" if audio else "",
        "English": _text(first.get("english") or ""),
        "Link": f'<a href="{html.escape(link)}">{format_timestamp(first["start"])}</a>',
        "Examples": examples,
        "SourceId": first["id"],
        "RecognitionCard": _toggle(options.vocab_recognition),
        "ProductionCard": _toggle(options.vocab_production),
    }


def _sentence_model() -> genanki.Model:
    return genanki.Model(
        SENTENCE_V2_MODEL_ID,
        SENTENCE_V2_NAME,
        fields=[{"name": name} for name in SENTENCE_V2_FIELDS],
        templates=SENTENCE_TEMPLATES,
        css=CSS_V2,
    )


def _vocab_model() -> genanki.Model:
    return genanki.Model(
        VOCAB_MODEL_ID,
        VOCAB_NAME,
        fields=[{"name": name} for name in VOCAB_FIELDS],
        templates=VOCAB_TEMPLATES,
        css=CSS_V2,
    )


def build_package(
    cards: list[dict],
    media_dir: Path,
    deck_name: str,
    options: ExportOptions | None = None,
    lang: Language | None = None,
    vocab_context: list[dict] | None = None,
) -> genanki.Package:
    """Assemble a genanki package with every card's notes and audio clips.

    Cards without `note_v` 2 go through the original note type into the original deck.
    New cards produce a v2 sentence note in `<deck>::Sentences` and, when they have a target
    word, one vocab note per headword in `<deck>::Vocab`.

    `vocab_context` is every card (oldest first). When given, a vocab note is built from all of
    them that mined the word, so exporting only some cards still writes the whole note.
    """
    options = options or ExportOptions()
    legacy_model, sentence_model, vocab_model = _model(), _sentence_model(), _vocab_model()
    decks: dict[int, genanki.Deck] = {}

    def deck(deck_id: int, name: str) -> genanki.Deck:
        if deck_id not in decks:
            decks[deck_id] = genanki.Deck(deck_id, name)
        return decks[deck_id]

    media_files: dict[str, str] = {}  # file name -> path, so a clip is only added once

    def attach_audio(fields: dict[str, str], *names: str) -> None:
        """Keep each [sound:...] only if its clip exists, and register the file."""
        for name in names:
            for match in re.finditer(r"\[sound:([^\]]+)\]", fields[name]):
                path = media_dir / match.group(1)
                if path.is_file():
                    media_files[match.group(1)] = str(path)
                else:
                    fields[name] = fields[name].replace(match.group(0), "")

    vocab_groups: dict[str, list[dict]] = {}
    for card in cards:
        card_lang = lang or get_language(card.get("lang", "zh"))
        if not is_v2(card):
            fields = note_fields(card, card_lang)
            attach_audio(fields, "Audio")
            deck(DECK_ID, deck_name).add_note(
                _Note(
                    model=legacy_model,
                    fields=[fields[name] for name in FIELDS],
                    guid=genanki.guid_for(card["id"]),
                    tags=["ytmine"],
                    modified=card_modified(card),
                )
            )
            continue

        entries = card_entries(card, card_lang)
        kind = card_kind(card, entries)
        if options.sentences and not card.get("vocab_only"):
            fields = sentence_fields_v2(card, card_lang, options)
            attach_audio(fields, "Audio")
            sentence_deck = f"{deck_name}::Sentences"
            deck_id = SENTENCES_DECK_ID
            if options.deck_split == "channel" and card.get("channel"):
                sentence_deck += f"::{_deck_part(card['channel'])}"
                deck_id = _deck_id("channel", card["channel"])
            elif options.deck_split == "video":
                sentence_deck += f"::{_deck_part(card.get('title') or card['video_id'])}"
                deck_id = _deck_id("video", card["video_id"])
            deck(deck_id, sentence_deck).add_note(
                _Note(
                    model=sentence_model,
                    fields=[fields[name] for name in SENTENCE_V2_FIELDS],
                    guid=genanki.guid_for("ytmine-sentence-v2", card["id"]),
                    tags=card_tags(card, kind),
                    modified=card_modified(card),
                )
            )
        if options.vocab and card.get("target"):
            vocab_groups.setdefault(vocab_key(card, card_lang), []).append(card)

    if vocab_context is not None and vocab_groups:
        complete: dict[str, list[dict]] = {key: [] for key in vocab_groups}
        for card in vocab_context:
            if is_v2(card) and card.get("target"):
                card_lang = lang or get_language(card.get("lang", "zh"))
                key = vocab_key(card, card_lang)
                if key in complete:
                    complete[key].append(card)
        vocab_groups = {key: group or vocab_groups[key] for key, group in complete.items()}

    for key, group in vocab_groups.items():
        card_lang = lang or get_language(group[0].get("lang", "zh"))
        fields = vocab_fields(group, card_lang, options)
        attach_audio(fields, "SentenceAudio", "Examples")
        tags: list[str] = []
        for card in group:
            kind = card_kind(card, card_entries(card, card_lang))
            tags += [t for t in card_tags(card, kind) if t not in tags]
        deck(VOCAB_DECK_ID, f"{deck_name}::Vocab").add_note(
            _Note(
                model=vocab_model,
                fields=[fields[name] for name in VOCAB_FIELDS],
                guid=genanki.guid_for("ytmine-vocab", key),
                tags=tags,
                modified=max(card_modified(c) for c in group),
            )
        )

    package = genanki.Package(list(decks.values()))
    package.media_files = list(media_files.values())
    return package


def note_count(package: genanki.Package) -> int:
    decks = package.decks if isinstance(package.decks, list) else [package.decks]
    return sum(len(d.notes) for d in decks)


def export_apkg(
    cards: list[dict],
    media_dir: Path,
    deck_name: str,
    out_path: Path,
    options: ExportOptions | None = None,
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build_package(cards, media_dir, deck_name, options).write_to_file(str(out_path))
    return out_path
