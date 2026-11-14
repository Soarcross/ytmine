import pytest

from ytmine.fetch import (
    EN_AUTO,
    EN_MANUAL,
    ZH_AUTO,
    ZH_MANUAL,
    FetchError,
    choose_track,
    parse_video_id,
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
