"""CC-CEDICT: a free Chinese-English dictionary, downloaded on first use.

CC-CEDICT is licensed CC BY-SA 4.0 and ytmine is MIT, so the data is never
copied into the repository. It is downloaded into the user cache directory,
parsed once into a small SQLite file, and looked up from there.

If the download fails (offline, site down) the app keeps working without
definitions; `Dictionary.status()` says what happened so the UI can tell the user.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from ytmine.lang.base import Entry

log = logging.getLogger(__name__)

CEDICT_URL = "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz"
SCHEMA_VERSION = "1"
REFRESH_AFTER_SECONDS = 90 * 24 * 3600  # try for a newer copy every three months
RETRY_AFTER_SECONDS = 300  # after a failed download, wait this long before trying again
DOWNLOAD_TIMEOUT = 60

_LINE_RE = re.compile(r"^(\S+) (\S+) \[([^\]]*)\] /(.*)/\s*$")
_SYLLABLE_RE = re.compile(r"([A-Za-züÜ:]+)([1-5])?")
_TONE_MARKS = {
    "a": "āáǎàa",
    "e": "ēéěèe",
    "i": "īíǐìi",
    "o": "ōóǒòo",
    "u": "ūúǔùu",
    "ü": "ǖǘǚǜü",
}
_SAYING_TAGS = ("(proverb)", "(saying)", "(common saying)", "(traditional saying)")


def download_cedict() -> bytes:
    """Fetch the gzipped CC-CEDICT file. Raises OSError if the network fails."""
    request = urllib.request.Request(CEDICT_URL, headers={"User-Agent": "ytmine"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
        return response.read()


def normalize(text: str) -> str:
    """Lookup key: letters and digits only, lower case. Punctuation and spaces are dropped."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def numbered_to_tone_marks(pinyin: str) -> str:
    """Turn CEDICT pinyin ("yi1 ju3 liang3 de2") into tone marks ("yī jǔ liǎng dé")."""

    def convert(match: re.Match) -> str:
        syllable, tone = match.group(1), match.group(2)
        syllable = syllable.replace("u:", "ü").replace("U:", "Ü")
        if tone is None or tone == "5":
            return syllable
        lower = syllable.lower()
        # The mark goes on a or e if present, else on the o of "ou", else on the last vowel.
        if "a" in lower:
            pos = lower.index("a")
        elif "e" in lower:
            pos = lower.index("e")
        elif "ou" in lower:
            pos = lower.index("o")
        else:
            vowels = [i for i, ch in enumerate(lower) if ch in "aeiouü"]
            if not vowels:
                return syllable
            pos = vowels[-1]
        marked = _TONE_MARKS[lower[pos]][int(tone) - 1]
        if syllable[pos].isupper():
            marked = marked.upper()
        return syllable[:pos] + marked + syllable[pos + 1 :]

    return _SYLLABLE_RE.sub(convert, pinyin)


def classify(simplified: str, definitions: list[str]) -> str:
    """word, chengyu, suyu or xiehouyu, from the tags CEDICT puts in its definitions.

    CEDICT tags idioms "(idiom)" and a few sayings "(proverb)", "(saying)" and so on.
    It has no tag for 歇后语, so xiehouyu is only reported if one ever appears.
    """
    text = " ".join(definitions).lower()
    if "(xiehouyu)" in text:
        return "xiehouyu"
    if any(tag in text for tag in _SAYING_TAGS):
        return "suyu"
    if "(idiom)" in text:
        # A four-character idiom is a chengyu. Longer or shorter ones are set sayings.
        return "chengyu" if len(normalize(simplified)) == 4 else "suyu"
    return "word"


def parse_cedict(text: str):
    """Yield (traditional, simplified, numbered_pinyin, definitions) for each entry line."""
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if match:
            trad, simp, pinyin, defs = match.groups()
            yield trad, simp, pinyin, [d for d in defs.split("/") if d]


def build_database(text: str, path: Path) -> int:
    """Write the SQLite lookup file for CEDICT `text` to `path`. Returns the row count."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    rows = []
    for trad, simp, pinyin, defs in parse_cedict(text):
        rows.append(
            (
                trad,
                simp,
                normalize(trad),
                normalize(simp),
                numbered_to_tone_marks(pinyin),
                json.dumps(defs, ensure_ascii=False),
                classify(simp, defs),
            )
        )
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(
            """
            CREATE TABLE entries (
                id INTEGER PRIMARY KEY, trad TEXT, simp TEXT, trad_key TEXT, simp_key TEXT,
                pinyin TEXT, defs TEXT, kind TEXT
            );
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )
        conn.executemany(
            "INSERT INTO entries (trad, simp, trad_key, simp_key, pinyin, defs, kind) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.execute("CREATE INDEX idx_simp ON entries (simp_key)")
        conn.execute("CREATE INDEX idx_trad ON entries (trad_key)")
        conn.execute("INSERT INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
        conn.commit()
    finally:
        conn.close()
    tmp.replace(path)
    return len(rows)


class Dictionary:
    """CC-CEDICT lookups backed by a SQLite file in the cache directory."""

    def __init__(
        self,
        cache_dir: Path,
        fetch: Callable[[], bytes] | None = None,
        min_entries: int = 1000,
    ):
        self.path = Path(cache_dir) / "cedict" / "cedict.sqlite"
        self._fetch = fetch or download_cedict
        self._min_entries = min_entries  # fewer than this means a bad download
        self._lock = threading.Lock()  # guards state and the sqlite connection
        self._ready = threading.Event()  # set when the load attempt has finished, either way
        self._state = "none"
        self._error: str | None = None
        self._failed_at = 0.0
        self._conn: sqlite3.Connection | None = None
        self._keys: set[str] = set()  # every normalized headword of 2+ characters
        self.max_length = 1

    # -- loading -----------------------------------------------------------

    def start(self) -> None:
        """Begin loading in the background. Safe to call again; it only retries after a failure."""
        with self._lock:
            if self._state in ("loading", "ready"):
                return
            if self._state == "unavailable" and time.time() - self._failed_at < RETRY_AFTER_SECONDS:
                return
            self._state, self._error = "loading", None
            self._ready.clear()
        threading.Thread(target=self._load, daemon=True, name="cedict-load").start()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a load attempt has finished. Returns True if the dictionary is usable."""
        self._ready.wait(timeout)
        return self.is_ready

    @property
    def is_ready(self) -> bool:
        return self._state == "ready"

    def status(self) -> dict:
        return {"state": self._state, "error": self._error}

    def _load(self) -> None:
        try:
            self._open_or_download()
            state, error = "ready", None
        except Exception as exc:  # noqa: BLE001 - any failure just means "no definitions"
            log.warning("CC-CEDICT is unavailable: %s", exc)
            state = "unavailable"
            error = (
                "The dictionary could not be downloaded, so definitions are off. "
                f"ytmine will try again later. ({exc})"
            )
        with self._lock:
            self._state, self._error = state, error
            if state == "unavailable":
                self._failed_at = time.time()
        self._ready.set()

    def _open_or_download(self) -> None:
        have = self._database_ok()
        stale = have and time.time() - self.path.stat().st_mtime > REFRESH_AFTER_SECONDS
        if not have or stale:
            try:
                data = gzip.decompress(self._fetch()).decode("utf-8")
                count = build_database(data, self.path)
                if count < self._min_entries:  # an error page or a truncated file
                    self.path.unlink(missing_ok=True)
                    raise ValueError(f"the download had only {count} entries")
            except Exception:
                if not have:
                    raise
                log.warning("Could not refresh CC-CEDICT, using the cached copy.")
        self._connect()

    def _database_ok(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            conn = sqlite3.connect(self.path)
            try:
                row = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return False
        return bool(row) and row[0] == SCHEMA_VERSION

    def _connect(self) -> None:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        keys: set[str] = set()
        longest = 1
        for simp_key, trad_key in conn.execute("SELECT simp_key, trad_key FROM entries"):
            for key in (simp_key, trad_key):
                if len(key) >= 2:
                    keys.add(key)
                    longest = max(longest, len(key))
        with self._lock:
            self._conn, self._keys, self.max_length = conn, keys, longest

    # -- queries (all return nothing until the dictionary is ready) --------

    def lookup(self, word: str) -> list[Entry]:
        """Entries whose simplified or traditional headword equals `word` (punctuation ignored)."""
        key = normalize(word)
        if not key or self._conn is None:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT trad, simp, pinyin, defs, kind FROM entries "
                "WHERE simp_key = ? OR trad_key = ? ORDER BY id",
                (key, key),
            ).fetchall()
        entries = []
        for trad, simp, pinyin, defs, kind in rows:
            # The headword is simplified, and the alternate form is the traditional spelling.
            alt = trad if trad != simp else ""
            entries.append(Entry(simp, alt, pinyin, tuple(json.loads(defs)), kind))
        return entries

    def has_key(self, key: str) -> bool:
        """True if `key` (already normalized, 2+ characters) is a headword."""
        return key in self._keys

    def multi_char_headwords(self):
        """Yield (key, kind) for every headword of 2+ characters, both scripts."""
        if self._conn is None:
            return
        with self._lock:
            rows = self._conn.execute(
                "SELECT simp_key, trad_key, kind FROM entries WHERE length(simp_key) >= 2"
            ).fetchall()
        for simp_key, trad_key, kind in rows:
            yield simp_key, kind
            if trad_key != simp_key:
                yield trad_key, kind
