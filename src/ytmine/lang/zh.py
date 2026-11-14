"""Mandarin Chinese: jieba for word segmentation, pypinyin for pinyin."""

from __future__ import annotations

import logging
import re
import threading

from ytmine.lang.base import Language

_CJK_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
# No space before closing punctuation or after opening punctuation in pinyin output.
_NO_SPACE_BEFORE = re.compile(r"\s+([，。！？、；：,.!?;:）)”’」』])")
_NO_SPACE_AFTER = re.compile(r"([（(“‘「『])\s+")
# Pinyin is Latin text, so it reads better with Latin punctuation.
_TO_LATIN = str.maketrans("，。！？、；：（）“”‘’「」『』", ',.!?,;:()""\'\'""""')
_SPACE_AFTER_MARK = re.compile(r"([,.!?;:])(?=[^\W\d_])")


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
