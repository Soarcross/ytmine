"""The Flask app: a small JSON API plus the static frontend.

ytmine is a single-user local tool, so the server keeps one "current video" in
memory. Mined cards are saved to disk through `History`.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from ytmine import ankiconnect, clips, export
from ytmine.config import Settings
from ytmine.deps import ffmpeg_install_help, find_ffmpeg
from ytmine.fetch import FetchError, VideoData, download_audio, fetch_video, watch_url
from ytmine.history import History
from ytmine.lang import get_language
from ytmine.subtitles import (
    Line,
    align_translation,
    clean_cues,
    is_unpunctuated,
    join_span,
    parse_vtt,
)

log = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
AUDIO_WAIT_SECONDS = 120
DICTIONARY_WAIT_SECONDS = 30  # how long /api/load waits for a first-run dictionary download
MAX_SPAN_LINES = 6  # a card can cover at most this many transcript lines
MAX_SPAN_SECONDS = 30.0


@dataclass
class Services:
    """The things that touch the outside world, swappable in tests."""

    fetch_video: Callable[[str, Path], VideoData] = fetch_video
    download_audio: Callable[[str, Path], Path] = download_audio
    cut_clip: Callable[..., None] = clips.cut_clip
    find_ffmpeg: Callable[[], str | None] = find_ffmpeg


@dataclass
class CurrentVideo:
    data: VideoData
    lines: list[Line]
    audio_ready: threading.Event = field(default_factory=threading.Event)
    audio_path: Path | None = None
    audio_error: str | None = None


def create_app(settings: Settings | None = None, services: Services | None = None) -> Flask:
    settings = settings or Settings.load()
    settings.ensure_dirs()
    services = services or Services()
    lang = get_language(settings.language)
    lang.setup(settings.cache_dir)  # starts loading the dictionary in the background
    history = History(settings.history_path)
    session_id = uuid.uuid4().hex[:12]
    current: dict[str, CurrentVideo | None] = {"video": None}

    app = Flask(__name__, static_folder=None)

    # -- protect the local server -----------------------------------------

    @app.before_request
    def only_local_pages():
        """Reject requests from other websites (DNS rebinding and cross-site POSTs)."""
        if urlsplit("//" + request.host).hostname not in ("localhost", "127.0.0.1", "::1"):
            abort(403)
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            abort(403)

    @app.errorhandler(403)
    def forbidden(_):
        return jsonify(error="Forbidden: ytmine only answers requests from its own page."), 403

    # -- helpers -----------------------------------------------------------

    def error(message: str, status: int = 400, hint: str | None = None):
        return jsonify(error=message, hint=hint), status

    def card_view(card: dict) -> dict:
        parts = lang.cloze(card["sentence"], card.get("target") or "", card.get("target_start"))
        return {
            **card,
            "audio_url": f"/api/cards/{card['id']}/audio" if card.get("audio_file") else None,
            "link": watch_url(card["video_id"], card["start"]),
            "parts": parts._asdict() if parts else None,
        }

    def top_kind(entries) -> str:
        """The most idiom-like kind among a span's entries (chengyu > suyu > xiehouyu > word)."""
        kinds = {e.kind for e in entries}
        for kind in ("chengyu", "suyu", "xiehouyu"):
            if kind in kinds:
                return kind
        return "word"

    def line_view(line: Line, index: int, covered: list[tuple[float, float]] = ()) -> dict:
        return {
            "index": index,
            "start": round(line.start, 3),
            "end": round(line.end, 3),
            "text": line.text,
            "english": line.english,
            "tokens": [
                [tok, lang.reading(tok), lang.is_word(tok)] for tok in lang.segment(line.text)
            ],
            "expressions": [
                {"start": s.start, "end": s.end, "text": s.text, "kind": top_kind(s.entries)}
                for s in lang.expressions(line.text)
            ],
            "mined": history.has_sentence(line.text)
            or any(start - 0.01 <= line.start and line.end <= end + 0.01 for start, end in covered),
        }

    def fetch_audio_in_background(video: CurrentVideo) -> None:
        try:
            video.audio_path = services.download_audio(video.data.video_id, settings.cache_dir)
        except FetchError as exc:
            video.audio_error = f"{exc.message} {exc.hint or ''}".strip()
        except Exception as exc:  # noqa: BLE001 - report anything to the user instead of dying
            log.exception("audio download failed")
            video.audio_error = f"Audio download failed: {exc}"
        finally:
            video.audio_ready.set()

    def cards_for(scope: str) -> list[dict]:
        return history.cards(session_id if scope == "session" else None)

    # -- pages and status --------------------------------------------------

    @app.get("/")
    def index():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/static/<path:name>")
    def static_files(name: str):
        return send_from_directory(STATIC_DIR, name)

    @app.get("/api/info")
    def info():
        ffmpeg = services.find_ffmpeg()
        return jsonify(
            ffmpeg=bool(ffmpeg),
            ffmpeg_help=None if ffmpeg else ffmpeg_install_help(),
            session_id=session_id,
            deck_name=settings.deck_name,
            dictionary=lang.dictionary_status(),
        )

    @app.get("/api/lookup")
    def lookup():
        """Dictionary entries for a word or expression: reading, definitions, kind, other script."""
        query = request.args.get("q", "").strip()
        if not query or len(query) > 60:
            return error("Give a word or phrase in ?q=.")
        status = lang.dictionary_status()
        entries = lang.lookup(query)
        return jsonify(
            query=query,
            entries=[e.to_dict() for e in entries],
            dictionary=status,
        )

    # -- loading a video ---------------------------------------------------

    @app.post("/api/load")
    def load():
        url = str((request.get_json(silent=True) or {}).get("url", "")).strip()
        if not url:
            return error("Paste a YouTube URL first.")
        try:
            data = services.fetch_video(url, settings.cache_dir)
        except FetchError as exc:
            return error(exc.message, exc.status, exc.hint)

        # The first run downloads the dictionary. Segmentation depends on it, so give it a moment.
        lang.wait_ready(DICTIONARY_WAIT_SECONDS)
        cues = parse_vtt(data.zh_vtt)
        unpunctuated = is_unpunctuated(clean_cues(cues, merge=False))
        lines = clean_cues(
            cues, continues_after=lang.continues_after, starts_sentence=lang.starts_sentence
        )
        if not lines:
            return error("The Chinese subtitles were empty after cleaning.", 422)
        if data.en_vtt:
            align_translation(lines, clean_cues(parse_vtt(data.en_vtt), merge=False))

        # A multi-line card matches no single line's text, so also mark lines by time.
        covered = [
            (c["start"], c["end"]) for c in history.cards() if c.get("video_id") == data.video_id
        ]
        video = CurrentVideo(data=data, lines=lines)
        current["video"] = video
        threading.Thread(target=fetch_audio_in_background, args=(video,), daemon=True).start()

        return jsonify(
            video_id=data.video_id,
            title=data.title,
            duration=data.duration,
            zh_source=data.zh_source,
            en_source=data.en_source,
            unpunctuated=unpunctuated,
            dictionary=lang.dictionary_status(),
            lines=[line_view(line, i, covered) for i, line in enumerate(lines)],
        )

    @app.get("/api/audio-status")
    def audio_status():
        video = current["video"]
        if video is None:
            return jsonify(state="none")
        if not video.audio_ready.is_set():
            return jsonify(state="downloading")
        if video.audio_error:
            return jsonify(state="error", error=video.audio_error)
        return jsonify(state="ready")

    @app.get("/api/audio")
    def current_audio():
        """The full audio track, for playback when YouTube refuses to embed the video."""
        video = current["video"]
        if video is None or not video.audio_ready.is_set() or video.audio_path is None:
            abort(404)
        return send_file(video.audio_path, conditional=True)

    # -- mining ------------------------------------------------------------

    @app.post("/api/mine")
    def mine():
        video = current["video"]
        if video is None:
            return error("Load a video first.")
        body = request.get_json(silent=True) or {}
        index = body.get("line_index")
        if not isinstance(index, int) or not 0 <= index < len(video.lines):
            return error("Unknown line.")
        last = body.get("line_end", index)
        if not isinstance(last, int) or not index <= last < len(video.lines):
            return error("Unknown line range.")
        span = video.lines[index : last + 1]
        if len(span) > MAX_SPAN_LINES or span[-1].end - span[0].start > MAX_SPAN_SECONDS:
            return error(
                f"A card can cover at most {MAX_SPAN_LINES} lines or "
                f"{MAX_SPAN_SECONDS:.0f} seconds."
            )
        sentence, offsets = join_span([line.text for line in span])
        start, end = span[0].start, span[-1].end
        english = " ".join(line.english for line in span if line.english)

        existing = history.find_by_sentence(sentence)
        if existing:
            return jsonify(error="Already mined.", duplicate=True, card_id=existing["id"]), 409

        ffmpeg = services.find_ffmpeg()
        if not ffmpeg:
            return error("ffmpeg is not installed.", 503, ffmpeg_install_help())
        if not video.audio_ready.wait(AUDIO_WAIT_SECONDS):
            return error("The audio is still downloading. Try again in a moment.", 503)
        if video.audio_error or video.audio_path is None:
            return error(video.audio_error or "No audio available.", 502)

        target, target_start = "", None
        if body.get("target_text"):
            target_line = body.get("target_line", index)
            token_index = body.get("target_index")
            offset = None
            if isinstance(target_line, int) and index <= target_line <= last:
                if isinstance(token_index, int):
                    offset = lang.token_offset(
                        video.lines[target_line].text, token_index, body["target_text"]
                    )
            if offset is None:
                return error("The selected word does not match this line.")
            target = body["target_text"]
            target_start = offsets[target_line - index] + offset

        start_ms = int(round(start * 1000))
        card_id = f"{video.data.video_id}_{start_ms}"
        if last > index:  # multi-line cards get their end too, so ids never collide
            card_id += f"_{int(round(end * 1000))}"
        audio_file = f"ytmine_{card_id}.mp3"
        clip_start, clip_length = clips.clip_window(
            start, end, settings.padding, video.data.duration
        )
        try:
            services.cut_clip(
                ffmpeg, video.audio_path, settings.media_dir / audio_file, clip_start, clip_length
            )
        except clips.ClipError as exc:
            return error(str(exc), 500)

        card = {
            "id": card_id,
            "lang": lang.code,
            "video_id": video.data.video_id,
            "title": video.data.title,
            "start": round(start, 3),
            "end": round(end, 3),
            "sentence": sentence,
            "pinyin": lang.reading_text(sentence),
            "english": english,
            "target": target,
            "target_start": target_start,
            "audio_file": audio_file,
            "session_id": session_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        history.add(card)
        return jsonify(card_view(card)), 201

    # -- the card list -----------------------------------------------------

    @app.get("/api/cards")
    def list_cards():
        scope = request.args.get("scope", "session")
        return jsonify(cards=[card_view(c) for c in reversed(cards_for(scope))])

    @app.patch("/api/cards/<card_id>")
    def edit_card(card_id: str):
        card = history.get(card_id)
        if card is None:
            return error("Card not found.", 404)
        body = request.get_json(silent=True) or {}
        changes: dict = {}

        for key in ("sentence", "english", "pinyin", "target"):
            if key in body:
                if not isinstance(body[key], str):
                    return error(f"'{key}' must be text.")
                changes[key] = body[key].strip()
        if not changes:
            return error("Nothing to change.")

        sentence = changes.get("sentence", card["sentence"])
        if not sentence:
            return error("The sentence cannot be empty.")
        if sentence != card["sentence"]:
            clash = history.find_by_sentence(sentence)
            if clash and clash["id"] != card_id:
                return error("Another card already has that sentence.", 409)
            if "pinyin" not in changes:
                changes["pinyin"] = lang.reading_text(sentence)
        target = changes.get("target", card.get("target") or "")
        if target:
            parts = lang.cloze(sentence, target, None)
            if parts is None:
                return error("The target word is not in the sentence.")
            changes["target_start"] = len(parts.before)
        else:
            changes["target_start"] = None

        return jsonify(card_view(history.update(card_id, changes)))

    @app.delete("/api/cards/<card_id>")
    def delete_card(card_id: str):
        removed = history.delete(card_id)
        if removed is None:
            return error("Card not found.", 404)
        audio = removed.get("audio_file")
        if audio:
            (settings.media_dir / audio).unlink(missing_ok=True)
        return jsonify(ok=True)

    @app.get("/api/cards/<card_id>/audio")
    def card_audio(card_id: str):
        card = history.get(card_id)
        if card is None or not card.get("audio_file"):
            abort(404)
        path = settings.media_dir / card["audio_file"]
        if not path.is_file():
            abort(404)
        return send_file(path, mimetype="audio/mpeg")

    # -- export ------------------------------------------------------------

    @app.get("/api/export.apkg")
    def export_deck():
        cards = cards_for(request.args.get("scope", "session"))
        if not cards:
            return error("No cards to export yet. Mine a line first.")
        name = f"ytmine-{time.strftime('%Y%m%d-%H%M%S')}.apkg"
        path = export.export_apkg(
            cards, settings.media_dir, settings.deck_name, settings.exports_dir / name
        )
        return send_file(path, as_attachment=True, download_name=name)

    @app.post("/api/anki/send")
    def send_to_anki():
        scope = (request.get_json(silent=True) or {}).get("scope", "session")
        cards = cards_for(scope)
        if not cards:
            return error("No cards to send yet. Mine a line first.")
        try:
            result = ankiconnect.send_cards(
                cards, settings.media_dir, settings.deck_name, settings.ankiconnect_url
            )
        except ankiconnect.AnkiConnectError as exc:
            return error(str(exc), 502)
        return jsonify(added=result.added, skipped=result.skipped, errors=result.errors)

    return app
