"""Mandarin Chinese: jieba for word segmentation, pypinyin for pinyin."""

from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from ytmine.lang.base import Entry, Language, Span
from ytmine.lang.cedict import Dictionary

log = logging.getLogger(__name__)
_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# No space before closing punctuation or after opening punctuation in pinyin output.
_NO_SPACE_BEFORE = re.compile(r"\s+([，。！？、；：,.!?;:）)”’」』])")
_NO_SPACE_AFTER = re.compile(r"([（(“‘「『])\s+")
# Pinyin is Latin text, so it reads better with Latin punctuation.
_TO_LATIN = str.maketrans("，。！？、；：（）“”‘’「」『』", ',.!?,;:()""\'\'""""')
_SPACE_AFTER_MARK = re.compile(r"([,.!?;:])(?=[^\W\d_])")


def _worth_reporting(entries, length: int, crosses_punctuation: bool) -> bool:
    """Keep matches a learner would want as one unit, and drop accidental ones.

    Idioms and sayings always count. A plain two-character headword is usually just two
    ordinary words side by side ("我去", or "多,大" across a comma), so plain words need
    three characters, and four if the match runs across punctuation.
    """
    if crosses_punctuation and length < 4:
        return False
    return length >= 3 or any(e.kind != "word" for e in entries)


class Chinese(Language):
    code = "zh"
    name = "Mandarin Chinese"
    continues_after = (
        "一个", "一些", "这个", "那个", "这种", "那种", "这些", "那些", "的", "和", "跟",
        "与", "在", "把", "被", "给", "对", "从", "比", "向", "往", "为了", "因为", "所以",
        "但是", "可是", "然后", "如果", "虽然", "而且", "或者", "还有", "以及", "就是",
        "可以", "想要", "需要", "应该", "要", "会", "能", "让", "叫", "叫做", "觉得",
        "认为", "是", "很", "非常", "特别", "比较", "最", "更", "我", "你", "他", "她",
        "它", "我们", "你们", "他们", "一",
    )  # fmt: skip
    starts_sentence = (
        "但是", "可是", "不过", "所以", "因此", "然后", "那么", "那", "其实", "首先",
        "其次", "最后", "另外", "而且", "因为", "如果", "比如", "好", "对", "嗯",
    )  # fmt: skip

    def __init__(self) -> None:
        self._jieba = None
        self._lock = threading.Lock()
        self._dictionary: Dictionary | None = None
        self._prepared = threading.Event()  # set once the dictionary has been given to jieba
        self._prepared.set()

    # -- dictionary --------------------------------------------------------

    def setup(self, cache_dir: Path) -> None:
        self.use_dictionary(Dictionary(cache_dir))

    def use_dictionary(self, dictionary: Dictionary | None) -> None:
        """Attach CC-CEDICT and start loading it, and feeding its words to jieba, in the background.

        Words are added to jieba once. After that the segmentation of a given sentence does
        not change, so token indexes the page already holds stay valid.
        """
        self._dictionary = dictionary
        if dictionary is None:
            self._prepared.set()
            return
        self._prepared.clear()
        dictionary.start()
        threading.Thread(target=self._prepare, args=(dictionary,), daemon=True).start()

    def _prepare(self, dictionary: Dictionary) -> None:
        try:
            if dictionary.wait():
                jieba = self._load_jieba()
                with self._lock:
                    self._feed_jieba(jieba.dt, dictionary)
        except Exception:  # noqa: BLE001 - segmentation still works without the extra words
            log.exception("could not add dictionary words to jieba")
        finally:
            if dictionary is self._dictionary:
                self._prepared.set()

    @staticmethod
    def _feed_jieba(tokenizer, dictionary: Dictionary) -> None:
        """Add CEDICT's multi-character headwords to a jieba tokenizer (`jieba.dt`)."""
        # jieba loads its own dictionary lazily, and loading replaces `FREQ` with a new dict.
        # Initialize first, or "words jieba already knows" looks empty and every word we add
        # would overwrite jieba's real frequency with ours.
        tokenizer.initialize()
        known = tokenizer.FREQ
        for word, kind in dictionary.multi_char_headwords():
            # Only add what jieba lacks. A tiny frequency is enough for a rare idiom: a whole
            # idiom beats any split into pieces, because the pieces' probabilities multiply.
            if word not in known:
                tokenizer.add_word(word, freq=3 if kind != "word" else 1)

    def wait_ready(self, timeout: float) -> bool:
        """Wait for the dictionary to finish loading (or fail). True if it is usable."""
        self._prepared.wait(timeout)
        return (
            self._dictionary is not None and self._dictionary.is_ready and self._prepared.is_set()
        )

    def dictionary_status(self) -> dict:
        if self._dictionary is None:
            return super().dictionary_status()
        return self._dictionary.status()

    def lookup(self, word: str) -> list[Entry]:
        if self._dictionary is None or not self._dictionary.is_ready:
            return []
        return self._dictionary.lookup(word)

    def expressions(self, text: str) -> list[Span]:
        """Dictionary entries spanning two or more jieba tokens (idioms, sayings, set phrases).

        Punctuation inside a match is ignored, so a saying with a comma in it still hits.
        Each start position keeps only its longest match; matches may overlap each other.
        """
        dictionary = self._dictionary
        if dictionary is None or not dictionary.is_ready or not text:
            return []
        # Normalized characters, each remembering where it sits in the original text.
        chars = [(i, ch.lower()) for i, ch in enumerate(text) if ch.isalnum()]
        flat = "".join(ch for _, ch in chars)
        tokens = self.segment(text)
        boundaries, pos = set(), 0
        for tok in tokens:
            boundaries.add(pos)
            pos += len(tok)
        boundaries.add(len(text))

        spans = []
        for n in range(len(chars)):
            for length in range(min(dictionary.max_length, len(chars) - n), 1, -1):
                if not dictionary.has_key(flat[n : n + length]):
                    continue
                start, end = chars[n][0], chars[n + length - 1][0] + 1
                # A hit that is already a single jieba token is just a word, not an expression.
                if any(start < b < end for b in boundaries):
                    entries = tuple(dictionary.lookup(flat[n : n + length]))
                    if _worth_reporting(entries, length, crosses_punctuation=end - start > length):
                        spans.append(Span(start, end, text[start:end], entries))
                break
        return spans

    def _load_jieba(self):
        # jieba builds its dictionary on first use (about a second), so do it lazily.
        with self._lock:
            if self._jieba is None:
                import jieba

                jieba.setLogLevel(logging.WARNING)
                self._jieba = jieba
        return self._jieba

    def segment(self, text: str) -> list[str]:
        tokens = list(self._load_jieba().cut(text))
        # Safety net: if jieba ever drops characters, fall back to one token per character.
        return tokens if "".join(tokens) == text else list(text)

    def is_word(self, token: str) -> bool:
        return bool(_CJK_RE.search(token)) or super().is_word(token)

    def reading(self, token: str) -> str:
        if not _CJK_RE.search(token):
            return ""
        from pypinyin import Style, pinyin

        return " ".join(syllable[0] for syllable in pinyin(token, style=Style.TONE))

    def reading_text(self, text: str) -> str:
        spaced = super().reading_text(text)
        spaced = _NO_SPACE_BEFORE.sub(r"\1", spaced)
        spaced = _NO_SPACE_AFTER.sub(r"\1", spaced).translate(_TO_LATIN)
        # One space after a mark that is followed by a word: "hǎo.nǐ" -> "hǎo. nǐ".
        spaced = _SPACE_AFTER_MARK.sub(r"\1 ", spaced)
        return re.sub(r"\s{2,}", " ", spaced).strip()
