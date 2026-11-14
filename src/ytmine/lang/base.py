"""The interface every language module implements.

To add a language, subclass `Language`, implement `segment` and `reading`,
and register the class in `ytmine/lang/__init__.py`. Everything else
(transcript display, mining, export) goes through this interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import NamedTuple


class ClozeParts(NamedTuple):
    before: str
    target: str
    after: str


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
