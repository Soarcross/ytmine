"""Download subtitles and audio from YouTube with yt-dlp.

This is the only module that talks to YouTube. Choosing which subtitle track to
use is a separate pure function (`choose_track`) so it can be tested without
the network.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

UPDATE_HINT = (
    "YouTube changes often and yt-dlp needs regular updates. "
    "Try: pip install -U yt-dlp (installed with pipx: pipx runpip ytmine install -U yt-dlp)."
)

# Subtitle language preferences, best first. Manual tracks beat auto-captions.
ZH_MANUAL = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK")
ZH_AUTO = ("zh-orig", "zh-Hans", "zh", "zh-CN", "zh-Hant", "zh-TW")
EN_MANUAL = ("en", "en-US", "en-GB")
EN_AUTO = ("en-orig", "en")


class FetchError(Exception):
    """A problem the user should see, with an optional suggestion."""

    def __init__(
        self, message: str, hint: str | None = None, status: int = 502, code: str | None = None
    ):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.status = status
        self.code = code  # machine-readable reason, e.g. "not_chinese"


NOT_CHINESE = "not_chinese"

# Video languages (YouTube metadata) that count as Chinese content.
CHINESE_LANGUAGES = ("zh", "cmn", "yue")


class Track(NamedTuple):
    lang: str
    is_auto: bool

    @property
    def label(self) -> str:
        return f"{'auto-captions' if self.is_auto else 'manual'} ({self.lang})"


@dataclass
class VideoData:
    video_id: str
    title: str
    duration: float | None
    zh_vtt: str
    zh_source: str
    en_vtt: str | None = None
    en_source: str | None = None
    channel: str = ""  # uploader name, used for tags and optional subdecks


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_URL_ID_RES = (
    re.compile(
        r"(?:youtube\.com|youtube-nocookie\.com)/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/|v/)([A-Za-z0-9_-]{11})"
    ),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
)


def parse_video_id(url: str) -> str:
    """Extract the 11-character video id from a YouTube URL (or a bare id)."""
    url = url.strip()
    if _ID_RE.match(url):
        return url
    for pattern in _URL_ID_RES:
        match = pattern.search(url)
        if match:
            return match.group(1)
    raise FetchError(
        "That doesn't look like a YouTube video URL.",
        "Paste a link like https://www.youtube.com/watch?v=... or https://youtu.be/...",
        status=400,
    )


def watch_url(video_id: str, start: float | None = None) -> str:
    """Canonical YouTube link, optionally jumping to `start` seconds."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    return url if start is None else f"{url}&t={int(start)}s"


def choose_track(
    manual: dict,
    auto: dict,
    manual_prefs: tuple[str, ...],
    auto_prefs: tuple[str, ...],
    prefix: str,
) -> Track | None:
    """Pick the best subtitle track from yt-dlp's `subtitles` / `automatic_captions` dicts.

    Order: preferred manual codes, any manual code starting with `prefix`,
    preferred auto codes, any auto code starting with `prefix`.
    """
    manual = {k: v for k, v in (manual or {}).items() if v and k != "live_chat"}
    auto = {k: v for k, v in (auto or {}).items() if v}
    for pool, prefs, is_auto in ((manual, manual_prefs, False), (auto, auto_prefs, True)):
        for lang in prefs:
            if lang in pool:
                return Track(lang, is_auto)
        for lang in sorted(pool):
            if lang.startswith(prefix):
                return Track(lang, is_auto)
    return None


def _is_chinese_code(code: str) -> bool:
    return code.lower().split("-")[0] in CHINESE_LANGUAGES


def chinese_track(info: dict) -> Track:
    """The Chinese track to use, or a `not_chinese` FetchError if this is not Chinese content.

    yt-dlp's `info` gives three hints. `language` is the video language the uploader set
    (often missing). `automatic_captions` lists YouTube's speech recognition as `<lang>-orig`
    plus machine translations of it (or of a manual track) into every language. So English
    videos usually "have" zh-Hans auto-captions, which are only a translation of the English.
    Auto-captions count as Chinese only when the recognised speech is Chinese, or when there is
    nothing a translation could have come from.
    """
    language = info.get("language") or ""
    if language and not _is_chinese_code(language):
        raise FetchError(
            f"This video is not in Chinese (YouTube lists its language as '{language}').",
            "ytmine works with videos spoken in Chinese.",
            status=422,
            code=NOT_CHINESE,
        )
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if v and k != "live_chat"}
    auto = {k: v for k, v in (info.get("automatic_captions") or {}).items() if v}

    track = choose_track(manual, {}, ZH_MANUAL, ZH_AUTO, "zh")
    if track:
        return track
    spoken = [k[: -len("-orig")] for k in auto if k.endswith("-orig")]
    if spoken:
        translated = not any(_is_chinese_code(k) for k in spoken)
    else:
        translated = bool(manual)  # auto zh with no speech track was translated from a manual one
    track = choose_track({}, auto, ZH_MANUAL, ZH_AUTO, "zh")
    if track and not translated:
        return track
    dropped = track is not None
    raise FetchError(
        "This video has no Chinese subtitles or auto-captions.",
        "If you can see subtitles in the picture, they are burned into the video and "
        "ytmine cannot read them. Try a video with a CC (closed captions) track."
        + (" YouTube's machine-translated Chinese captions are not used." if dropped else ""),
        status=422,
        code=NOT_CHINESE,
    )


# yt-dlp metadata by video id, so a probe followed by a load asks YouTube only once.
_INFO_TTL = 600.0
_INFO_MAX = 32
_info_cache: dict[str, tuple[float, dict]] = {}
_info_lock = threading.Lock()


def video_info(video_id: str) -> dict:
    """yt-dlp's metadata for a video (no download), cached for a few minutes."""
    now = time.monotonic()
    with _info_lock:
        hit = _info_cache.get(video_id)
        if hit and now - hit[0] < _INFO_TTL:
            return hit[1]
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError, ExtractorError
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FetchError("yt-dlp is not installed.", "Run: pip install -U yt-dlp") from exc
    try:
        with yt_dlp.YoutubeDL(_base_options()) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False)
    except (DownloadError, ExtractorError) as exc:
        raise _wrap_download_error(exc) from exc
    with _info_lock:
        for key in [k for k, (t, _) in _info_cache.items() if now - t >= _INFO_TTL]:
            del _info_cache[key]
        while len(_info_cache) >= _INFO_MAX:
            del _info_cache[next(iter(_info_cache))]
        _info_cache[video_id] = (now, info)
    return info


def probe_video(url: str) -> bool:
    """True if the video is Chinese content ytmine can load. Raises FetchError on other problems."""
    try:
        chinese_track(video_info(parse_video_id(url)))
    except FetchError as exc:
        if exc.code == NOT_CHINESE:
            return False
        raise
    return True


def _wrap_download_error(exc: Exception) -> FetchError:
    detail = str(exc).replace("ERROR: ", "").strip().splitlines()[-1][:300]
    return FetchError(f"yt-dlp could not fetch this video: {detail}", UPDATE_HINT)


def _base_options() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 3,
    }


def _find_subtitle_file(directory: Path, video_id: str, lang: str) -> Path:
    exact = directory / f"{video_id}.{lang}.vtt"
    if exact.is_file():
        return exact
    for candidate in sorted(directory.glob(f"{video_id}.*.vtt")):
        if candidate.name.split(".")[-2] == lang:
            return candidate
    raise FetchError(
        f"yt-dlp did not save the '{lang}' subtitles.",
        "YouTube may be rate limiting subtitle downloads. Wait a minute and retry. " + UPDATE_HINT,
    )


def fetch_video(url: str, cache_dir: Path) -> VideoData:
    """Fetch video info plus Chinese (and, if present, English) subtitles."""
    video_id = parse_video_id(url)
    page = watch_url(video_id)
    work_dir = cache_dir / video_id
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError, ExtractorError
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise FetchError("yt-dlp is not installed.", "Run: pip install -U yt-dlp") from exc

    info = video_info(video_id)
    manual, auto = info.get("subtitles") or {}, info.get("automatic_captions") or {}
    zh = chinese_track(info)
    en = choose_track(manual, auto, EN_MANUAL, EN_AUTO, "en")

    options = _base_options() | {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [zh.lang] + ([en.lang] if en else []),
        "subtitlesformat": "vtt",
        "outtmpl": str(work_dir / "%(id)s.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(page, download=True)
    except (DownloadError, ExtractorError) as exc:
        raise _wrap_download_error(exc) from exc

    zh_text = _find_subtitle_file(work_dir, video_id, zh.lang).read_text(encoding="utf-8")
    en_text = None
    if en:
        try:
            en_text = _find_subtitle_file(work_dir, video_id, en.lang).read_text(encoding="utf-8")
        except FetchError:
            en = None  # English is optional; carry on without it

    return VideoData(
        video_id=video_id,
        title=info.get("title") or video_id,
        duration=info.get("duration"),
        zh_vtt=zh_text,
        zh_source=zh.label,
        en_vtt=en_text,
        en_source=en.label if en else None,
        channel=str(info.get("channel") or info.get("uploader") or ""),
    )


def download_audio(video_id: str, cache_dir: Path) -> Path:
    """Download the best audio-only stream (no re-encoding) and return its path."""
    work_dir = cache_dir / video_id
    work_dir.mkdir(parents=True, exist_ok=True)

    def existing() -> Path | None:
        files = [p for p in work_dir.glob("audio.*") if p.suffix not in (".part", ".ytdl")]
        return files[0] if files else None

    if (found := existing()) is not None:
        return found

    import yt_dlp
    from yt_dlp.utils import DownloadError, ExtractorError

    options = _base_options() | {
        # m4a first: it plays in every browser, including Safari, when the
        # YouTube player is blocked and ytmine plays this file itself.
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": str(work_dir / "audio.%(ext)s"),
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(watch_url(video_id), download=True)
    except (DownloadError, ExtractorError) as exc:
        raise _wrap_download_error(exc) from exc
    if (found := existing()) is None:
        raise FetchError("yt-dlp finished but no audio file was saved.", UPDATE_HINT)
    return found
