"""The interface every language module implements.

To add a language, subclass `Language`, implement `segment` and `reading`,
and register the class in `ytmine/lang/__init__.py`. Everything else
(transcript display, mining, export) goes through this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import NamedTuple


class ClozeParts(NamedTuple):
    before: str
    target: str
    after: str


@dataclass(frozen=True)
class Entry:
    """One dictionary entry."""

    headword: str  # as written in the language's main script (simplified for Chinese)
    alt_form: str  # the other script's spelling (traditional for Chinese), "" if none
    reading: str  # pronunciation with tone marks
    definitions: tuple[str, ...] = field(default_factory=tuple)
    kind: str = "word"  # word, chengyu, suyu or xiehouyu

    def to_dict(self) -> dict:
        data = asdict(self)
        data["definitions"] = list(self.definitions)
        return data


class Span(NamedTuple):
    """A multi-word expression found in a sentence, as character offsets into it."""

    start: int
    end: int  # exclusive
    text: str  # exactly text[start:end], punctuation inside included
    entries: tuple[Entry, ...]


class Language(ABC):
    code: str  # short code stored on each card, e.g. "zh"
    name: str  # human-readable name
    blank: str = "[...]"  # what replaces the target word on the card front
    # Used to rebuild sentences from captions that have no punctuation:
    # words a clause practically never ends on, and words that usually open one.
    continues_after: tuple[str, ...] = ()
    starts_sentence: tuple[str, ...] = ()

    @abstractmethod
    def segment(self, text: str) -> list[str]:
        """Split text into tokens (words and punctuation). Joining them must give `text` back."""

    @abstractmethod
    def reading(self, token: str) -> str:
        """Pronunciation of one token (pinyin for Chinese), or "" if it has none."""

    def setup(self, cache_dir: Path) -> None:  # noqa: B027 - optional hook, not abstract
        """Called once by the app. Languages with a dictionary start loading it here."""

    def wait_ready(self, timeout: float) -> bool:
        """Wait up to `timeout` seconds for the dictionary. True if it is usable."""
        return False

    def lookup(self, word: str) -> list[Entry]:
        """Dictionary entries for `word`. Languages without a dictionary return []."""
        return []

    def expressions(self, text: str) -> list[Span]:
        """Idioms and set phrases in `text` that span more than one token."""
        return []

    def dictionary_status(self) -> dict:
        """{"state": "ready" | "loading" | "unavailable" | "none", "error": str | None}."""
        return {"state": "none", "error": None}

    def is_word(self, token: str) -> bool:
        """True if the token can be picked as a cloze target."""
        return any(ch.isalnum() for ch in token)

    def reading_text(self, text: str) -> str:
        """Pronunciation of a whole sentence. Tokens without a reading are kept as written."""
        return " ".join(self.reading(tok) or tok for tok in self.segment(text) if tok.strip())

    def cloze(self, text: str, target: str, start: int | None = None) -> ClozeParts | None:
        """Split `text` around `target`.

        `start` is the target's character offset when known (it picks the right
        occurrence if the word appears twice). Returns None if the target is not
        in the text. Languages with inflection can override this to match word
        forms; the default matches the exact string.
        """
        if not target:
            return None
        if start is None or text[start : start + len(target)] != target:
            start = text.find(target)
        if start < 0:
            return None
        return ClozeParts(text[:start], target, text[start + len(target) :])

    def token_offset(self, text: str, index: int, expected: str) -> int | None:
        """Character offset of token number `index`, if it really is `expected`."""
        tokens = self.segment(text)
        if 0 <= index < len(tokens) and tokens[index] == expected:
            return sum(len(tok) for tok in tokens[:index])
        return None
