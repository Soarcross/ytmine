# ytmine

ytmine is a local web app for sentence mining Mandarin from YouTube videos. You paste a video URL, watch it next to a scrolling Chinese transcript, press one key to mine a line, and get Anki cards with audio.

It runs on your own computer. There is no account, no server, no tracking and no analytics.

## What it does

- Plays the video in the page with the YouTube player and shows the Chinese subtitles beside it. The current line is highlighted and scrolls into view.
- Mines a line with one keypress. ytmine cuts a short mp3 clip, adds pinyin, and attaches the English line when the video has English subtitles.
- Lets you click any word to blank it out (a cloze card), with word boundaries from jieba.
- Looks words up in the CC-CEDICT dictionary (pinyin, definitions, simplified and traditional forms) and recognises idioms (chengyu) and sayings, so 一举两得 stays one unit and a saying with a comma in it is found as a whole. Without the dictionary everything else still works.
- Cleans up messy subtitles: removes the repeated text in auto-captions, strips tags and sound cues like [音乐], and joins sentences that were split across cues.
- Exports an Anki `.apkg` file with the audio included, or has a running Anki import it through AnkiConnect. Mined lines become sentence cards (listening and reading), and a clicked word also becomes a vocab card, filed in `Sentences` and `Vocab` subdecks with tags.
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

### Cards and decks

Each mined line becomes a sentence note. If you clicked a word first, it also becomes a vocab note for that word.

Sentence notes have two cards:

- Listening: the audio and the sentence with your chosen word blanked. The back shows the full sentence, the word, pinyin, English, the definition and a link to the video at that moment.
- Reading: the sentence with the word highlighted and no audio. The audio plays on the back.

Vocab notes (one per word, not one per sentence) have a recognition card, which shows the word and answers with pinyin, definition and your example sentence with its audio, and an optional production card, which shows the definition and the sentence with the word blanked. If you mine the same word again from another video, ytmine keeps the first note and adds the new sentence to it as an extra example.

Open Card settings under the card list to switch card types on or off. Production is off by default. You can also put sentences into subdecks by channel or by video. Choices are saved in `config.json`.

Decks are laid out as `<deck name>::Sentences` and `<deck name>::Vocab`, with `<deck name>::Sentences::<channel>` if you split by channel. Notes are tagged `ytmine`, `ytmine::video::<id>` and `ytmine::channel::<name>`, and idioms and sayings also get `ytmine::kind::chengyu` or `ytmine::kind::suyu`, so you can filter by them in Anki's browser. The card styling follows Anki's night mode.

### Sending cards to Anki

Download .apkg gives you a file to import. The deck and note type IDs are fixed, so importing a newer export updates the same deck and notes instead of making copies.

Send to Anki makes the same file and asks Anki to import it through the [AnkiConnect](https://ankiweb.net/shared/info/2055492159) add-on (code 2055492159). Anki must be running. Both routes use the same note types and the same note IDs, so you can use either one, or both, without getting duplicates. Anki reads the file from your disk, so if Anki runs in a sandbox that cannot see it (Flatpak or Snap), Send to Anki reports an error. Use Download .apkg in that case.

Each note in an `.apkg` carries the time the card was mined or last edited in ytmine. When you import a newer export, Anki only replaces notes you have not changed in Anki since then, so your edits in Anki are kept. A vocab note counts as changed when you mine another example for it.

### If you used an earlier version

Cards mined before the vocab and subdeck update stay on the original "ytmine Mandarin sentence" note type and in the original deck, and they keep exporting that way. Nothing in your existing Anki collection is changed or moved. New mining goes to the new note types, in the `Sentences` and `Vocab` subdecks.

If you used Send to Anki before, those notes were added one by one with random IDs. Sending those cards again now imports them with a stable ID, which Anki does not match to the old copies, so you would get a second copy. Delete the old copies in Anki first if you want a single one.

### Where your data lives

| What | Where |
| --- | --- |
| Mined history and cards | `mined_history.json` in your user data directory |
| Audio clips, exported decks | `media/` and `exports/` in the same directory |
| Downloaded audio, subtitles and the dictionary | your user cache directory (`ytmine --clear-cache` deletes it) |
| Optional settings | `config.json` in your user config directory |

The exact directories are chosen by [platformdirs](https://pypi.org/project/platformdirs/). ytmine prints the data directory when it starts. You can override the locations with the `YTMINE_DATA_DIR` and `YTMINE_CACHE_DIR` environment variables.

`config.json` accepts these keys, all optional:

```json
{
  "padding": 0.3,
  "deck_name": "ytmine Mandarin",
  "ankiconnect_url": "http://127.0.0.1:8765",
  "deck_split": "none",
  "card_types": {
    "listening": true,
    "reading": true,
    "vocab_recognition": true,
    "vocab_production": false
  }
}
```

`padding` is the number of seconds of audio added before and after each line. `ankiconnect_url` must point at localhost. `deck_split` is `none`, `channel` or `video`. The card settings in the web page write `card_types` and `deck_split` here.

## FAQ

Which videos work?
Any public video that has Chinese manual subtitles or Chinese auto-captions. Age-restricted, private and members-only videos will not load. Subtitles that are part of the picture (burned in) cannot be read; the video needs a real caption track.

The video says "This video is unavailable".
The owner has blocked it from playing outside YouTube. ytmine switches to the audio it downloaded, so the transcript, hotkeys and mining still work; you just don't see the picture.

Why does a card's audio start or end a little off?
Subtitle timing is only as good as the uploader made it. Auto-captions are looser than manual ones. Raise `padding` in `config.json` if clips cut off too early.

The Chinese came from auto-captions and has mistakes. What now?
Auto-caption text is machine generated, and it does get words wrong: it can turn 一席崩塌 into 一夕, for example. Dictionary lookups and idiom detection work on the text as captioned, so a wrong word gives a wrong lookup. Use the Edit button in the card panel to fix a card before exporting.

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
Three things. yt-dlp talks to YouTube to fetch subtitles and audio, and your browser loads the YouTube player (from youtube-nocookie.com) to play the video. The first time you run ytmine it also downloads the CC-CEDICT dictionary (about 4 MB) from mdbg.net into your cache directory. It is kept there and re-checked about every three months. If the download fails, ytmine works without definitions and tries again later. The optional Send to Anki button talks to AnkiConnect on localhost. There is no analytics, telemetry or update check. The server only listens on 127.0.0.1 and rejects requests from other websites.

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

Dictionary data comes from [CC-CEDICT](https://cc-cedict.org/) (distributed by MDBG), which is licensed under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). ytmine downloads it at run time and does not include it in this repository.
