"""Parse WebVTT captions and turn them into clean transcript lines.

Raw YouTube captions are messy. Auto-captions repeat the previous line inside
every new cue ("rolling" captions), contain inline timing tags, and manual
captions often split one sentence across several cues. `clean_cues` fixes all
of that and keeps start/end times.

The module is plain Python with no network or language-specific code, so it is
easy to test with small fixture files.
"""

from __future__ import annotations

import html
import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Cue:
    """One raw caption cue, exactly as it appears in the file (minus parsing)."""

    start: float
    end: float
    text: str  # may contain several lines separated by "\n"


@dataclass
class Line:
    """One cleaned transcript line."""

    start: float
    end: float
    text: str
    english: str = ""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# "00:01:02.500" or "01:02.500" (hours are optional); SRT-style commas also work.
_TS = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
_TIMING_RE = re.compile(rf"^\s*{_TS}\s*-->\s*{_TS}")


def _seconds(hours: str | None, minutes: str, seconds: str, millis: str) -> float:
    return (
        int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, "0")) / 1000
    )


def parse_vtt(text: str) -> list[Cue]:
    """Parse WebVTT text into raw cues. Header, NOTE and STYLE blocks are skipped."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    cues: list[Cue] = []
    for block in re.split(r"\n{2,}", text):
        lines = block.split("\n")
        for i, line in enumerate(lines):
            match = _TIMING_RE.match(line)
            if match:
                start = _seconds(*match.group(1, 2, 3, 4))
                end = _seconds(*match.group(5, 6, 7, 8))
                cues.append(Cue(start, end, "\n".join(lines[i + 1 :])))
                break
    return cues


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]*>")  # <c>, </c>, <00:00:01.500>, <i> ...
# A whole line that is only a sound cue such as [音乐], (笑) or ♪♪.
_SOUND_CUE_RE = re.compile(r"^\s*(?:[\[(（【][^\])）】]*[\])）】]|[♪♫♬\s]+)\s*$")

_SENTENCE_END = "。！？!?….．"
_CLOSERS = "”’」』）)\"'"
_CONTINUATION = "，、,；;：:—-"


def _clean_line(raw: str) -> str:
    text = _TAG_RE.sub("", raw)
    text = html.unescape(text)
    text = text.replace(" ", " ").replace("​", "")
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"^>>\s*", "", text)


def _is_sound_cue(text: str) -> bool:
    return bool(_SOUND_CUE_RE.match(text))


def _join(a: str, b: str) -> str:
    """Join two text fragments, adding a space only where both sides are ASCII (English)."""
    if a and b and a[-1].isascii() and b[0].isascii():
        return f"{a} {b}"
    return a + b


def _ends_sentence(text: str) -> bool:
    return text.rstrip(_CLOSERS).endswith(tuple(_SENTENCE_END))


def _ends_with_continuation(text: str) -> bool:
    return text.rstrip(_CLOSERS).endswith(tuple(_CONTINUATION))


def _dedupe_rolling(cues: list[Cue], rolling_gap: float) -> list[Line]:
    """Remove the repeated text that auto-captions put in every cue.

    A rolling cue looks like "previous line\\nnew line", often followed by a
    tiny (~10 ms) cue that only repeats the last line. We keep just the new
    text. Cues that are not contiguous with the previous one are never treated
    as repeats, so a speaker really saying "好的" twice survives.
    """
    out: list[Line] = []
    prev_lines: list[str] = []
    prev_end: float | None = None

    for cue in sorted(cues, key=lambda c: (c.start, c.end)):
        raw = [_clean_line(part) for part in cue.text.split("\n")]
        raw = [part for part in raw if part and not _is_sound_cue(part)]
        contiguous = prev_end is not None and cue.start - prev_end <= rolling_gap
        tiny = cue.end - cue.start < 0.1
        fresh = (
            [p for p in raw if p not in prev_lines]
            if contiguous and (len(raw) > 1 or tiny)
            else raw
        )
        prev_lines, prev_end = raw, cue.end

        if not fresh:
            if raw and out:  # pure repeat: the previous line simply lasts longer
                out[-1].end = max(out[-1].end, cue.end)
            continue

        text = ""
        for part in fresh:
            text = _join(text, part)

        # Text that grows word by word ("你" -> "你好" -> "你好吗") replaces itself.
        if (
            out
            and cue.start - out[-1].end <= rolling_gap
            and text.startswith(out[-1].text)
            and len(text) > len(out[-1].text)
        ):
            out[-1].text = text
            out[-1].end = max(out[-1].end, cue.end)
            continue

        out.append(Line(cue.start, cue.end, text))
    return out


def _should_merge(
    prev: Line, line: Line, max_gap: float, max_duration: float, max_chars: int, min_chars: int
) -> bool:
    if _ends_sentence(prev.text):
        return False
    if line.start - prev.end > max_gap:
        return False
    if line.end - prev.start > max_duration:
        return False
    if len(prev.text) + len(line.text) > max_chars:
        return False
    # Only glue when the earlier piece clearly is a fragment: it ends on a comma
    # or similar, or it is very short. Unpunctuated auto-captions therefore keep
    # their own line breaks instead of collapsing into one giant line.
    return _ends_with_continuation(prev.text) or len(prev.text.replace(" ", "")) < min_chars


def _merge_fragments(lines: list[Line], **limits: float) -> list[Line]:
    merged: list[Line] = []
    for line in lines:
        if merged and _should_merge(merged[-1], line, **limits):
            prev = merged[-1]
            prev.text = _join(prev.text, line.text)
            prev.end = line.end
        else:
            merged.append(Line(line.start, line.end, line.text))
    return merged


def join_span(texts: list[str]) -> tuple[str, list[int]]:
    """Join consecutive line texts into one sentence for a multi-line card.

    Returns the joined text and the character offset where each piece starts.
    A comma is added between two CJK pieces when the first has no punctuation
    of its own; ASCII pieces get a space.
    """
    text, offsets = "", []
    for piece in texts:
        if (
            text
            and not text[-1].isascii()
            and not text.rstrip(_CLOSERS).endswith(tuple(_SENTENCE_END + _CONTINUATION))
        ):
            text += "，" if not piece[:1].isascii() else ""
        elif text and text[-1].isascii() and piece[:1].isascii():
            text += " "
        offsets.append(len(text))
        text += piece
    return text, offsets


def is_unpunctuated(lines: list[Line], threshold: float = 0.1) -> bool:
    """True if almost no line carries sentence or clause punctuation.

    Some channels upload manual captions with no punctuation at all, cut every
    few seconds. Those need a different merge rule (see `_rebuild_sentences`).
    """
    if not lines:
        return False
    marks = set(_SENTENCE_END + _CONTINUATION)
    marked = sum(1 for line in lines if marks.intersection(line.text))
    return marked / len(lines) < threshold


def _rebuild_sentences(
    lines: list[Line],
    continues_after: tuple[str, ...],
    starts_sentence: tuple[str, ...],
    pause: float,
    short_chars: int,
    max_duration: float,
    max_chars: int,
) -> list[Line]:
    """Join unpunctuated caption fragments back into sentence-sized lines.

    A line is joined to the next one when it ends on a word a clause cannot end
    on (e.g. 一个, 的, 和), or when it is short and the next one follows
    without a pause. A line that opens a new sentence (e.g. 但是, 所以) is
    never joined to the one before it. Fragments joined because of a short
    pause get a comma between them.
    """
    out: list[Line] = []
    for line in lines:
        text = line.text.strip()
        if out:
            prev = out[-1]
            fits = len(prev.text) + len(text) <= max_chars and line.end - prev.start <= max_duration
            dangling = bool(continues_after) and prev.text.endswith(continues_after)
            opens = bool(starts_sentence) and text.startswith(starts_sentence)
            quick = line.start - prev.end < pause and len(prev.text.replace(" ", "")) < short_chars
            if fits and not opens and (dangling or quick):
                joiner = "" if dangling or not prev.text or prev.text[-1].isascii() else "，"
                prev.text = _join(prev.text + joiner, text)
                prev.end = line.end
                continue
        out.append(Line(line.start, line.end, text))
    return out


def _fix_overlaps(lines: list[Line]) -> None:
    """Make each line end no later than the next one starts."""
    for line, nxt in zip(lines, lines[1:], strict=False):
        if nxt.start < line.end:
            line.end = max(nxt.start, line.start + 0.1)


def clean_cues(
    cues: list[Cue],
    *,
    merge: bool = True,
    rolling_gap: float = 0.3,
    max_gap: float = 1.0,
    max_duration: float = 15.0,
    max_chars: int = 50,
    min_chars: int = 8,
    continues_after: tuple[str, ...] = (),
    starts_sentence: tuple[str, ...] = (),
    pause: float = 0.35,
    short_chars: int = 12,
    rebuild_max_chars: int = 40,
) -> list[Line]:
    """Turn raw cues into clean lines.

    Steps: strip tags and sound cues, drop rolling-caption repeats, optionally
    merge fragments split mid-sentence, then trim overlapping end times.

    Captions with (almost) no punctuation are merged with `_rebuild_sentences`
    instead, using the language's `continues_after` and `starts_sentence` words.
    """
    lines = _dedupe_rolling(cues, rolling_gap)
    if merge and is_unpunctuated(lines):
        lines = _rebuild_sentences(
            lines,
            continues_after,
            starts_sentence,
            pause=pause,
            short_chars=short_chars,
            max_duration=max_duration,
            max_chars=rebuild_max_chars,
        )
    elif merge:
        lines = _merge_fragments(
            lines,
            max_gap=max_gap,
            max_duration=max_duration,
            max_chars=max_chars,
            min_chars=min_chars,
        )
    _fix_overlaps(lines)
    return lines


# ---------------------------------------------------------------------------
# Time lookup and translation alignment
# ---------------------------------------------------------------------------


def line_index_at(
    lines: list[Line],
    t: float,
    *,
    starts: list[float] | None = None,
    hold: float | None = None,
) -> int:
    """Index of the line playing at time `t`, or -1 if none.

    By default a line stays "current" until the next one starts, which keeps
    the highlight steady through pauses in speech. Pass `hold` (seconds) to
    drop the highlight once `t` is more than `hold` past the line's end.
    The frontend (static/app.js) uses the same rule.
    """
    if starts is None:
        starts = [line.start for line in lines]
    i = bisect_right(starts, t) - 1
    if i < 0:
        return -1
    if hold is not None and t > lines[i].end + hold:
        return -1
    return i


def align_translation(lines: list[Line], translation: list[Line], slack: float = 1.0) -> None:
    """Fill `line.english` by giving each translation cue to the line it overlaps most.

    A cue goes to the line that contains its midpoint. A cue in a short gap
    (up to `slack` seconds) goes to the nearest line; anything further away is
    dropped.
    """
    if not lines:
        return
    starts = [line.start for line in lines]
    buckets: dict[int, list[str]] = defaultdict(list)
    for cue in translation:
        mid = (cue.start + cue.end) / 2
        i = line_index_at(lines, mid, starts=starts)
        if i < 0:
            target = 0 if lines[0].start - mid <= slack else -1
        elif mid <= lines[i].end + slack:
            target = i
        elif i + 1 < len(lines) and lines[i + 1].start - mid <= slack:
            target = i + 1
        else:
            target = -1
        if target >= 0 and (not buckets[target] or buckets[target][-1] != cue.text):
            buckets[target].append(cue.text)
    for i, texts in buckets.items():
        lines[i].english = " ".join(texts)
