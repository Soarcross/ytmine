# Contributing to ytmine

Thanks for helping. This is a small project and the goal is to keep it simple, so a short issue or a small pull request is always welcome.

## Getting set up

```bash
git clone https://github.com/Soarcross/ytmine.git
cd ytmine
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

You also need ffmpeg on your PATH to run the one test that cuts real audio. That test is skipped if ffmpeg is missing.

Before opening a pull request, run:

```bash
pytest
ruff check .
ruff format --check .      # ruff format . fixes formatting
```

GitHub Actions runs the same checks on Windows, macOS and Linux.

## Project layout

```
src/ytmine/
  cli.py          command-line entry point (starts the server, opens the browser)
  app.py          Flask app and JSON API
  fetch.py        yt-dlp: subtitles, audio, choosing a subtitle track
  subtitles.py    VTT parsing, cleaning, line-at-time lookup, English alignment
  clips.py        clip time calculation and the ffmpeg call
  history.py      mined_history.json (duplicate detection and card storage)
  export.py       Anki .apkg export (fixed deck and note type IDs)
  ankiconnect.py  optional "Send to Anki"
  config.py       per-user directories and settings
  deps.py         ffmpeg detection and install help
  lang/           language-specific logic (segmentation, reading, cloze)
  static/         the frontend: plain HTML, CSS and JS, no build step
tests/            pytest tests with small hand-written fixtures
```

## Ground rules

- No tracking, analytics or extra network calls. The only network traffic is yt-dlp talking to YouTube and optional AnkiConnect on localhost.
- Use `pathlib` for paths and `platformdirs` for per-user directories. Do not assume Windows, macOS or Linux.
- No build step for the frontend. Keep it plain HTML, CSS and JavaScript.
- Never put real YouTube content in the repository: no captions, audio, thumbnails or transcripts. Test fixtures must be text you wrote yourself.
- Do not change `MODEL_ID` or `DECK_ID` in `export.py`. Users rely on them to update their existing decks. If you change the note fields or templates, mention it in the pull request so we can plan a migration.
- Keep the code simple and commented. A short comment that says why is better than a clever trick.

## Adding a language

Everything language-specific lives behind the `Language` class in `src/ytmine/lang/base.py`.

1. Create `src/ytmine/lang/<code>.py` with a subclass of `Language`.
2. Implement `segment(text)`, which splits text into tokens that join back into the original string, and `reading(token)`, which returns a pronunciation (or an empty string).
3. Override `is_word`, `reading_text`, `cloze` or `blank` only if the defaults do not fit the language.
4. Register the class in `src/ytmine/lang/__init__.py`.
5. Add tests like the ones in `tests/test_lang_zh.py`.

Other parts of the app still assume Chinese in a few places, mainly the subtitle language preferences in `fetch.py`, the Anki note type in `export.py`, and the `language` setting. Language support is a work in progress, so expect to touch those files and say so in your pull request.

## Reporting bugs

Use the bug report template. Include your operating system, Python version, the output of `ytmine --version` and `pip show yt-dlp`, and the error message. If a video fails to load, update yt-dlp first (`pip install -U yt-dlp`) because that fixes most YouTube problems. Do not attach captions or audio from videos.

## Code of conduct

Everyone taking part is expected to follow the [Code of Conduct](CODE_OF_CONDUCT.md).
