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

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

from ytmine import ankiconnect, clips, export
from ytmine.config import DECK_SPLITS, DEFAULT_CARD_TYPES, Settings
from ytmine.deps import ffmpeg_install_help, find_ffmpeg
from ytmine.fetch import FetchError, VideoData, download_audio, fetch_video, watch_url
from ytmine.history import History
from ytmine.lang import get_language
from ytmine.pairing import TOKEN_HEADER, is_extension_origin, load_or_create_token, token_matches
from ytmine.store import CaptionFixes, KnownWords, line_key
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
    originals: dict[int, str] = field(default_factory=dict)  # line index -> caption as downloaded


def create_app(settings: Settings | None = None, services: Services | None = None) -> Flask:
    settings = settings or Settings.load()
    settings.ensure_dirs()
    services = services or Services()
    lang = get_language(settings.language)
    lang.setup(settings.cache_dir)  # starts loading the dictionary in the background
    history = History(settings.history_path)
    fixes = CaptionFixes(settings.fixes_path)
    known = KnownWords(settings.known_path)
    session_id = uuid.uuid4().hex[:12]
    current: dict[str, CurrentVideo | None] = {"video": None}

    app = Flask(__name__, static_folder=None)
    pairing_token = load_or_create_token(settings.token_file)
    app.config["PAIRING_TOKEN"] = pairing_token

    # -- protect the local server -----------------------------------------

    @app.before_request
    def only_local_pages():
        """Reject requests from other websites (DNS rebinding and cross-site POSTs)."""
        if urlsplit("//" + request.host).hostname not in ("localhost", "127.0.0.1", "::1"):
            abort(403)
        given = request.headers.get(TOKEN_HEADER)
        has_token = token_matches(given, pairing_token)
        if given is not None and not has_token:
            abort(403)  # a token that is present must be right, whatever else the request says
        origin = request.headers.get("Origin")
        if not origin:
            # Browsers leave Origin off GET requests that are same-origin, plain navigations and
            # no-cors loads (<img>, <script>), and Chrome leaves it off an extension's GETs too.
            # The Sec-Fetch headers tell these apart. A request that is clearly from some other
            # context needs the token; curl-style clients (no Sec-Fetch headers), navigations and
            # our own page do not.
            site = request.headers.get("Sec-Fetch-Site")
            mode = request.headers.get("Sec-Fetch-Mode")
            outside = (site in ("cross-site", "same-site") and mode != "navigate") or (
                site == "none" and mode == "cors"
            )
            if outside and not has_token:
                abort(403)
            return None
        if urlsplit(origin).netloc == request.host:
            return None  # our own page
        # A browser extension is the one other caller. It must be paired: Origin
        # chrome-extension:// or moz-extension:// plus the token. A web page never qualifies,
        # whatever it sends, and neither does an extension without the token.
        if is_extension_origin(origin):
            if request.method == "OPTIONS":
                # The browser's preflight cannot carry the token. It gets only CORS headers,
                # no data, and the real request that follows is checked.
                request.environ["ytmine.cors_origin"] = origin
                return Response(status=204)
            if has_token:
                request.environ["ytmine.cors_origin"] = origin
                return None
        abort(403)

    @app.after_request
    def allow_paired_extension(response):
        origin = request.environ.get("ytmine.cors_origin")
        if origin:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = f"Content-Type, {TOKEN_HEADER}"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE"
            response.headers["Vary"] = "Origin"
        return response

    @app.errorhandler(403)
    def forbidden(_):
        return jsonify(
            error="Forbidden: ytmine only answers requests from its own page "
            "and from the paired browser extension."
        ), 403

    # -- helpers -----------------------------------------------------------

    def error(message: str, status: int = 400, hint: str | None = None):
        return jsonify(error=message, hint=hint), status

    def wrong_video(body: dict):
        """An error response if the caller means a different video than the one loaded.

        The server keeps one current video. A client that names the video it is showing (the
        browser extension does) is stopped from mining into whichever video was loaded last.
        """
        wanted, video = body.get("video_id"), current["video"]
        if wanted and video is not None and wanted != video.data.video_id:
            return error(
                "ytmine has a different video loaded now (another tab?). Reload this page.", 412
            )
        return None

    def card_view(card: dict) -> dict:
        parts = lang.cloze(card["sentence"], card.get("target") or "", card.get("target_start"))
        return {
            **card,
            "audio_url": f"/api/cards/{card['id']}/audio" if card.get("audio_file") else None,
            "link": watch_url(card["video_id"], card["start"]),
            "parts": parts._asdict() if parts else None,
        }

    def target_info(target: str) -> dict:
        """What the dictionary says about a target word, saved on the card when it is mined.

        Saved so a deck can be exported later without the dictionary (it may be offline).
        """
        entries = [e.to_dict() for e in lang.lookup(target)][:4] if target else []
        return {
            "target_entries": entries,
            "target_kind": export.card_kind({}, entries) if entries else "",
        }

    def top_kind(entries) -> str:
        """The most idiom-like kind among a span's entries (chengyu > suyu > xiehouyu > word)."""
        kinds = {e.kind for e in entries}
        for kind in ("chengyu", "suyu", "xiehouyu"):
            if kind in kinds:
                return kind
        return "word"

    def line_view(line: Line, index: int, covered: list[tuple[float, float]] = ()) -> dict:
        video = current["video"]
        return {
            "edited": video is not None and index in video.originals,
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
        # Re-apply corrections the user made to this video's captions earlier.
        saved = fixes.for_video(data.video_id)
        for i, line in enumerate(lines):
            fixed = saved.get(line_key(line.start))
            if fixed and fixed != line.text:
                video.originals[i] = line.text
                line.text = fixed
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
        if (problem := wrong_video(body)) is not None:
            return problem
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
                line_text = video.lines[target_line].text
                if isinstance(token_index, int):
                    offset = lang.token_offset(line_text, token_index, body["target_text"])
                elif isinstance(body.get("target_offset"), int):
                    # An expression span (an idiom, or a saying with punctuation in it).
                    given, text = body["target_offset"], body["target_text"]
                    if given >= 0 and line_text[given : given + len(text)] == text:
                        offset = given
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
            "note_v": 2,  # new note types; cards without this stay on the original one
            "channel": video.data.channel,
            **target_info(target),
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
        if target != (card.get("target") or ""):
            changes.update(target_info(target))

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

    # -- caption fixes -----------------------------------------------------

    @app.patch("/api/lines/<int:index>")
    def fix_line(index: int):
        """Correct the text of one caption line. Auto-captions often get words wrong."""
        video = current["video"]
        if video is None or not 0 <= index < len(video.lines):
            return error("Unknown line.", 404)
        body = request.get_json(silent=True) or {}
        if (problem := wrong_video(body)) is not None:
            return problem
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return error("The caption cannot be empty.")
        text = " ".join(text.split())
        if len(text) > 300:
            return error("That caption is too long (300 characters at most).")
        line = video.lines[index]
        original = video.originals.get(index, line.text)
        key = line_key(line.start)
        if text == original:  # changed back: forget the fix
            video.originals.pop(index, None)
            fixes.clear(video.data.video_id, key)
        else:
            video.originals[index] = original
            fixes.set(video.data.video_id, key, text)
        line.text = text
        return jsonify(line_view(line, index))

    # -- mined and known words ---------------------------------------------

    def word_forms(word: str) -> set[str]:
        """The word as written plus its simplified and traditional spellings."""
        forms = {word}
        for entry in lang.lookup(word):
            forms |= {entry.headword, entry.alt_form} - {""}
        return forms

    def vocab_view() -> dict:
        mined: set[str] = set()
        for card in history.cards():
            target = card.get("target")
            if not target:
                continue
            mined.add(target)
            for entry in card.get("target_entries") or []:
                mined |= {entry["headword"], entry["alt_form"]} - {""}
        return {"mined": sorted(mined), "known": sorted(known.all())}

    @app.get("/api/vocab")
    def get_vocab():
        return jsonify(vocab_view())

    @app.post("/api/known")
    def set_known():
        body = request.get_json(silent=True) or {}
        word = body.get("word")
        if not isinstance(word, str) or not word.strip() or len(word) > 60:
            return error("Give the word to mark.")
        known.update(word_forms(word.strip()), bool(body.get("known", True)))
        return jsonify(vocab_view())

    # -- settings ----------------------------------------------------------

    def settings_view() -> dict:
        return {
            "card_types": settings.card_types,
            "deck_split": settings.deck_split,
            "deck_name": settings.deck_name,
        }

    @app.get("/api/settings")
    def get_settings():
        return jsonify(settings_view())

    @app.put("/api/settings")
    def put_settings():
        body = request.get_json(silent=True) or {}
        types = body.get("card_types", {})
        if not isinstance(types, dict) or any(
            k not in DEFAULT_CARD_TYPES or not isinstance(v, bool) for k, v in types.items()
        ):
            return error(
                "card_types must map listening, reading, vocab_recognition and "
                "vocab_production to true or false."
            )
        split = body.get("deck_split", settings.deck_split)
        if split not in DECK_SPLITS:
            return error(f"deck_split must be one of: {', '.join(DECK_SPLITS)}.")
        deck_name = body.get("deck_name", settings.deck_name)
        if not isinstance(deck_name, str) or not deck_name.strip() or "::" in deck_name:
            return error("The deck name cannot be empty or contain '::'.")
        settings.card_types.update(types)
        settings.deck_split, settings.deck_name = split, deck_name.strip()
        try:
            settings.save_card_settings()
        except OSError as exc:
            return error(f"Could not save the settings: {exc}", 500)
        return jsonify(settings_view())

    # -- export ------------------------------------------------------------

    def build_export(scope: str):
        """Build the package for `scope`, or return an error response."""
        cards = cards_for(scope)
        if not cards:
            return None, error("No cards to export yet. Mine a line first.")
        package = export.build_package(
            cards,
            settings.media_dir,
            settings.deck_name,
            export.ExportOptions.from_settings(settings),
        )
        if export.note_count(package) == 0:
            return None, error("Every card type is switched off in the settings.")
        return package, None

    @app.get("/api/export.apkg")
    def export_deck():
        package, problem = build_export(request.args.get("scope", "session"))
        if problem:
            return problem
        name = f"ytmine-{time.strftime('%Y%m%d-%H%M%S')}.apkg"
        path = settings.exports_dir / name
        package.write_to_file(str(path))
        return send_file(path, as_attachment=True, download_name=name)

    @app.post("/api/anki/send")
    def send_to_anki():
        """Have Anki import the same .apkg the Download button gives you."""
        scope = (request.get_json(silent=True) or {}).get("scope", "session")
        package, problem = build_export(scope)
        if problem:
            return problem
        path = settings.exports_dir / f"ytmine-send-{time.strftime('%Y%m%d-%H%M%S')}.apkg"
        package.write_to_file(str(path))
        try:
            ankiconnect.import_package(path, settings.ankiconnect_url)
        except ankiconnect.AnkiConnectError as exc:
            return error(str(exc), 502)
        return jsonify(notes=export.note_count(package))

    return app
