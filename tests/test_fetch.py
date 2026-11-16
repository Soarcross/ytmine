import pytest

from ytmine import fetch
from ytmine.fetch import (
    EN_AUTO,
    EN_MANUAL,
    NOT_CHINESE,
    ZH_AUTO,
    ZH_MANUAL,
    FetchError,
    chinese_track,
    choose_track,
    parse_video_id,
    probe_video,
    watch_url,
)

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VID}",
        f"https://www.youtube.com/watch?list=PL123&v={VID}&t=30s",
        f"https://youtu.be/{VID}?si=abc",
        f"https://www.youtube.com/shorts/{VID}",
        f"https://www.youtube.com/embed/{VID}",
        f"https://m.youtube.com/watch?v={VID}",
        VID,
    ],
)
def test_parse_video_id(url):
    assert parse_video_id(url) == VID


def test_parse_video_id_rejects_other_urls():
    with pytest.raises(FetchError):
        parse_video_id("https://example.com/watch?v=whatever")


def test_watch_url_with_timestamp():
    assert watch_url(VID, 12.7) == f"https://www.youtube.com/watch?v={VID}&t=12s"


def track(manual, auto, kind="zh"):
    prefs = (ZH_MANUAL, ZH_AUTO) if kind == "zh" else (EN_MANUAL, EN_AUTO)
    return choose_track({k: [1] for k in manual}, {k: [1] for k in auto}, *prefs, kind)


def test_manual_chinese_beats_auto():
    assert track(["zh-Hans"], ["zh-orig"]) == ("zh-Hans", False)


def test_simplified_preferred_over_traditional_manual():
    assert track(["zh-Hant", "zh-Hans"], []) == ("zh-Hans", False)


def test_falls_back_to_any_manual_zh_variant():
    assert track(["zh-SG"], ["zh-orig"]) == ("zh-SG", False)


def test_falls_back_to_auto_captions():
    assert track(["en"], ["zh-orig", "zh-Hans", "en"]) == ("zh-orig", True)


def test_no_chinese_returns_none():
    assert track(["en", "fr"], ["en"]) is None


def test_live_chat_is_not_a_subtitle_track():
    assert choose_track({"live_chat": [1]}, {}, ZH_MANUAL, ZH_AUTO, "zh") is None


def test_english_prefers_manual_then_auto():
    assert track(["en-GB"], ["en"], "en") == ("en-GB", False)
    assert track([], ["en-orig", "en"], "en") == ("en-orig", True)


# chinese_track: shapes taken from real yt-dlp metadata (2026-11-16).


def info(language=None, manual=(), auto=()):
    return {
        "language": language,
        "subtitles": {k: [1] for k in manual},
        "automatic_captions": {k: [1] for k in auto},
    }


def not_chinese(i):
    with pytest.raises(FetchError) as exc:
        chinese_track(i)
    assert exc.value.code == NOT_CHINESE
    assert exc.value.status == 422
    return exc.value


def test_chinese_video_with_manual_track():
    # gnCXffOg7T8 / -ZXVm_6b3rk: no language set, manual zh, auto zh is a translation of it
    assert chinese_track(info(manual=["zh", "en"], auto=["zh", "en", "fr"])) == ("zh", False)


def test_english_video_with_translated_chinese_auto_captions():
    # dQw4w9WgXcQ: language en, speech recognised as English, zh-Hans is a machine translation
    err = not_chinese(info("en", manual=["en", "ja"], auto=["en-orig", "en", "zh-Hans", "zh-Hant"]))
    assert "en" in err.message


def test_language_unset_but_speech_is_english():
    err = not_chinese(info(auto=["en-orig", "en", "zh-Hans"]))
    assert "machine-translated" in err.hint


def test_korean_video_even_with_manual_chinese_subtitles():
    # 9bZkp7q19f0 is language ko; a fan-made zh track does not make it Chinese content
    not_chinese(info("ko", manual=["zh-Hans", "en"], auto=["ko-orig", "zh-Hans"]))


def test_auto_chinese_translated_from_a_manual_english_track():
    not_chinese(info(manual=["en"], auto=["zh-Hans", "en"]))


def test_no_tracks_at_all():
    # kJQP7kiw5Fk: nothing to read
    err = not_chinese(info())
    assert "machine-translated" not in err.hint


def test_chinese_speech_recognition_is_accepted():
    assert chinese_track(info(auto=["zh-orig", "zh-Hans", "en"])) == ("zh-orig", True)
    assert chinese_track(info("zh-CN", auto=["zh-Hans-orig", "zh-Hans"])).is_auto


def test_auto_chinese_with_nothing_to_translate_from_is_accepted():
    assert chinese_track(info(auto=["zh-Hans"])) == ("zh-Hans", True)


def test_chinese_language_codes():
    for language in ("zh", "zh-Hans", "zh-TW", "cmn", "yue-HK"):
        assert chinese_track(info(language, manual=["zh-TW"])) == ("zh-TW", False)


def test_probe_video(monkeypatch):
    monkeypatch.setattr(fetch, "video_info", lambda v: info("en", auto=["en-orig", "zh-Hans"]))
    assert probe_video(f"https://youtu.be/{VID}") is False
    monkeypatch.setattr(fetch, "video_info", lambda v: info(manual=["zh-Hans"]))
    assert probe_video(VID) is True


def test_video_info_is_cached(monkeypatch):
    import yt_dlp

    calls = []

    class FakeYDL:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            calls.append(url)
            return {"id": VID}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(fetch, "_info_cache", {})
    assert fetch.video_info(VID) == {"id": VID}
    assert fetch.video_info(VID) == {"id": VID}
    assert len(calls) == 1


def test_probe_video_passes_other_errors_on(monkeypatch):
    def broken(video_id):
        raise FetchError("yt-dlp could not fetch this video: boom")

    monkeypatch.setattr(fetch, "video_info", broken)
    with pytest.raises(FetchError):
        probe_video(VID)
