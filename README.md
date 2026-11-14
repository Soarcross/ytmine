# ytmine

ytmine is a local web app for sentence mining Mandarin from YouTube videos. You paste a video URL, watch it next to a scrolling Chinese transcript, press one key to mine a line, and get Anki cards with audio.

It runs on your own computer. There is no account, no server, no tracking and no analytics.

## What it does

- Plays the video in the page with the YouTube player and shows the Chinese subtitles beside it. The current line is highlighted and scrolls into view.
- Mines a line with one keypress. ytmine cuts a short mp3 clip, adds pinyin, and attaches the English line when the video has English subtitles.
- Lets you click any word to blank it out (a cloze card), with word boundaries from jieba.
- Cleans up messy subtitles: removes the repeated text in auto-captions, strips tags and sound cues like [音乐], and joins sentences that were split across cues.
- Exports an Anki `.apkg` file with the audio included, or sends the cards straight to a running Anki through AnkiConnect.
- Remembers what you have mined in a history file, so the same sentence is never mined twice.

## Screenshots

Screenshots will go here before the first release.

- Main view: `docs/screenshots/main.png` (video, transcript, card panel)
- Pinyin and cloze: `docs/screenshots/cloze.png`
- Anki card: `docs/screenshots/anki-card.png`

## Install

You need Python 3.10 or newer and ffmpeg. ffmpeg is only used to cut the audio clips.

### Windows

```powershell
winget install Gyan.FFmpeg
winget install Python.Python.3.12
pip install pipx
pipx ensurepath
pipx install git+https://github.com/Soarcross/ytmine.git
```

Open a new terminal after installing so the updated PATH is picked up. If you prefer Chocolatey or Scoop, `choco install ffmpeg` and `scoop install ffmpeg` work too.

### macOS

```bash
brew install ffmpeg pipx
pipx ensurepath
pipx install git+https://github.com/Soarcross/ytmine.git
```

### Linux

Debian and Ubuntu:

```bash
sudo apt install ffmpeg pipx
pipx ensurepath
pipx install git+https://github.com/Soarcross/ytmine.git
```

On Fedora use `sudo dnf install ffmpeg-free pipx`, on Arch `sudo pacman -S ffmpeg python-pipx`.

### From a clone (any system)

```bash
git clone https://github.com/Soarcross/ytmine.git
cd ytmine
pip install .
```

### Updating

YouTube changes often, and yt-dlp, the library that talks to it, needs frequent updates. If a video fails to load, run:

```bash
pipx runpip ytmine install -U yt-dlp   # installed with pipx
pip install -U yt-dlp                  # installed with pip
```

## Usage

Start the app:

```bash
ytmine
# or
python -m ytmine
```

Your browser opens at `http://127.0.0.1:8787`. If the port is busy, ytmine picks the next free one, or you can choose one with `--port 9000`.

1. Paste a YouTube URL and press Load. The video needs Chinese subtitles. Manual subtitles are preferred, and auto-captions are the fallback. The status line under the video tells you which kind was used.
2. Play the video. Press `M` when you hear a sentence you want.
3. To blank out a word, click it in the transcript before pressing `M`. Click it again to undo.
4. When you are done, press Download .apkg and open the file with Anki, or press Send to Anki.

Audio downloads in the background after you load a video. The first mine may wait a few seconds for it.

### Hotkeys

| Key | Action |
| --- | --- |
| M | Mine the current line. If the line changed in the last 1.5 seconds, mine the previous line instead, since you are usually a beat late. A line you clicked is always mined as is. |
| R | Replay the line you just mined (within 8 seconds), otherwise the current line. |
| Space | Play or pause. |
| Left / Right | Jump to the previous or next line. |
| Shift+Left / Shift+Right | Add the previous or next line to the card, for sentences split over several lines (up to 6 lines or 30 seconds). Shift+click a line does the same. |
| Esc | Clear the line selection and the blanked word. |

Click a line to seek to it. Click a word to set it as the blank. If you scroll the transcript yourself, auto-scroll pauses until you press Resume auto-scroll. The Pinyin checkbox shows pinyin above the characters.

A thin blue bar on the left of a line shows which line `M` would mine right now. A green check marks lines you have already mined.

Some channels upload subtitles with no punctuation, cut every few seconds. ytmine notices this and joins the pieces back into sentences, using words a Chinese clause rarely ends on (一个, 的, 和) and words that usually start one (但是, 所以). The status line tells you when it did this. If a card still cuts a sentence in half, add lines with Shift+Left or Shift+Right before pressing M.

### Sending cards to Anki

Download .apkg gives you a file to import. The deck and note type IDs are fixed, so importing a newer export updates the same deck and notes instead of making copies.

Send to Anki adds the notes directly. Anki must be running with the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on installed (code 2055492159). If Anki is closed, ytmine tells you so. Cards already in the deck are skipped.

Use one route or the other for a given deck. Importing an `.apkg` and sending through AnkiConnect create separate note types with the same name.

Each note in an `.apkg` carries the time the card was mined or last edited in ytmine. When you import a newer export, Anki only replaces notes you have not changed in Anki since then, so your edits in Anki are kept.

The card front shows the sentence (with the blank if you picked a word) and plays the audio. The back shows the full sentence, the blanked word, pinyin, English, and a link that opens the video at that moment.

### Where your data lives

| What | Where |
| --- | --- |
| Mined history and cards | `mined_history.json` in your user data directory |
| Audio clips, exported decks | `media/` and `exports/` in the same directory |
| Downloaded audio and subtitles | your user cache directory (`ytmine --clear-cache` deletes it) |
| Optional settings | `config.json` in your user config directory |

The exact directories are chosen by [platformdirs](https://pypi.org/project/platformdirs/). ytmine prints the data directory when it starts. You can override the locations with the `YTMINE_DATA_DIR` and `YTMINE_CACHE_DIR` environment variables.

`config.json` accepts these keys, all optional:

```json
{
  "padding": 0.3,
  "deck_name": "ytmine Mandarin",
  "ankiconnect_url": "http://127.0.0.1:8765"
}
```

`padding` is the number of seconds of audio added before and after each line. `ankiconnect_url` must point at localhost.

## FAQ

Which videos work?
Any public video that has Chinese manual subtitles or Chinese auto-captions. Age-restricted, private and members-only videos will not load. Subtitles that are part of the picture (burned in) cannot be read; the video needs a real caption track.

The video says "This video is unavailable".
The owner has blocked it from playing outside YouTube. ytmine switches to the audio it downloaded, so the transcript, hotkeys and mining still work; you just don't see the picture.

Why does a card's audio start or end a little off?
Subtitle timing is only as good as the uploader made it. Auto-captions are looser than manual ones. Raise `padding` in `config.json` if clips cut off too early.

The Chinese came from auto-captions and has mistakes. What now?
Auto-caption text is machine generated. Use the Edit button in the card panel to fix a card before exporting.

Do I need to click on the page for the hotkeys to work?
Hotkeys work whenever the page has focus. If you click inside the video, ytmine takes focus back so the keys keep working. Typing in a text field disables the hotkeys while you type.

Can I mine a sentence again?
Delete its card in the panel. That also removes it from the history.

It says ffmpeg was not found.
Install it with the commands in the Install section and restart your terminal. ytmine checks for it at startup and shows the instructions for your system.

A video will not load and the message mentions yt-dlp.
Update yt-dlp (see Updating above). If that does not help, YouTube may be rate limiting you. Wait a few minutes and try again.

Does it work for other languages?
Not yet. Word segmentation, pinyin and cloze live in `src/ytmine/lang/`, behind a small interface, so adding a language is a contained change. See CONTRIBUTING.md.

What does it send over the network?
Only two things. yt-dlp talks to YouTube to fetch subtitles and audio, and your browser loads the YouTube player (from youtube-nocookie.com) to play the video. The optional Send to Anki button talks to AnkiConnect on localhost. There is no analytics, telemetry or update check. The server only listens on 127.0.0.1 and rejects requests from other websites.

## Personal study use only

ytmine is a tool for your own language study. It does not host, store for others or redistribute any video, audio or subtitle content. Audio clips and subtitles are saved on your computer for your personal use, and the repository contains no YouTube content. Your cards and exported decks contain material from videos you chose, so keep them private and do not share or publish them.

You are responsible for following YouTube's Terms of Service and the copyright rules that apply where you live. ytmine is not affiliated with or endorsed by YouTube, Google or Anki.

## Development

```bash
git clone https://github.com/Soarcross/ytmine.git
cd ytmine
pip install -e ".[dev]"
pytest
ruff check .
ruff format --check .
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the project layout and how to add a language.

## License

MIT. See [LICENSE](LICENSE).
