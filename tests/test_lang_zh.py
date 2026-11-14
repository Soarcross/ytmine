import pytest

from ytmine.lang import get_language


@pytest.fixture(scope="module")
def zh():
    return get_language("zh")


def test_segment_round_trips_text(zh):
    text = "我喜欢学习中文，你呢？ABC 123"
    assert "".join(zh.segment(text)) == text


def test_reading_gives_tone_marked_pinyin(zh):
    assert zh.reading("你好") == "nǐ hǎo"
    assert zh.reading("，") == ""
    assert zh.reading("hello") == ""


def test_reading_text_has_no_space_before_punctuation(zh):
    result = zh.reading_text("你好，世界。")
    assert result.startswith("nǐ hǎo,") or result.startswith("nǐ hǎo，")
    assert " ，" not in result and " 。" not in result


def test_is_word(zh):
    assert zh.is_word("学习")
    assert not zh.is_word("，")


def test_cloze_splits_around_target(zh):
    parts = zh.cloze("我喜欢学习中文", "学习")
    assert (parts.before, parts.target, parts.after) == ("我喜欢", "学习", "中文")


def test_cloze_uses_start_offset_to_pick_the_occurrence(zh):
    parts = zh.cloze("他说他喜欢", "他", start=2)
    assert (parts.before, parts.after) == ("他说", "喜欢")


def test_cloze_missing_target_returns_none(zh):
    assert zh.cloze("你好", "再见") is None
    assert zh.cloze("你好", "") is None


def test_token_offset_checks_the_token(zh):
    text = "我喜欢学习中文"
    tokens = zh.segment(text)
    assert zh.token_offset(text, 0, tokens[0]) == 0
    assert zh.token_offset(text, 1, tokens[1]) == len(tokens[0])
    assert zh.token_offset(text, 0, "不是") is None
    assert zh.token_offset(text, 99, "我") is None


def test_unknown_language_is_rejected():
    with pytest.raises(ValueError):
        get_language("xx")


def test_reading_text_uses_latin_punctuation(zh):
    assert zh.reading_text("是的,很暖和。你吃了吗?") == "shì de, hěn nuǎn huo. nǐ chī le ma?"
    assert "3.5" in zh.reading_text("我有3.5个")
