"""CC-CEDICT parsing, lookup and the expression scan. No network: a small fixture is used."""

import gzip
import sqlite3

import pytest

from ytmine.lang import cedict
from ytmine.lang.cedict import Dictionary, classify, numbered_to_tone_marks
from ytmine.lang.zh import Chinese

SAYING = "三个臭皮匠，顶个诸葛亮"


@pytest.fixture
def mini_gz(fixture_text):
    return gzip.compress(fixture_text("cedict_mini.txt").encode("utf-8"))


@pytest.fixture
def dictionary(tmp_path, mini_gz):
    d = Dictionary(tmp_path, fetch=lambda: mini_gz, min_entries=1)
    d.start()
    assert d.wait(10)
    return d


@pytest.fixture
def zh(dictionary):
    lang = Chinese()
    lang.use_dictionary(dictionary)
    assert lang.wait_ready(10)
    return lang


def test_tone_marks():
    assert numbered_to_tone_marks("yi1 ju3 liang3 de2") == "yī jǔ liǎng dé"
    assert numbered_to_tone_marks("nu:3") == "nǚ"
    assert numbered_to_tone_marks("wo3 men5") == "wǒ men"
    assert numbered_to_tone_marks("Zhong1 guo2") == "Zhōng guó"
    assert numbered_to_tone_marks("hou4 ou3 liu2 hui4") == "hòu ǒu liú huì"  # ou, iu, ui rules


def test_classify():
    assert classify("一举两得", ["one action (idiom)"]) == "chengyu"
    assert classify("三个臭皮匠，顶个诸葛亮", ["x (idiom)"]) == "suyu"  # not four characters
    assert classify("水到渠成", ["a (proverb)"]) == "suyu"
    assert classify("x", ["a (common saying)"]) == "suyu"
    assert classify("学习", ["to study"]) == "word"


def test_plain_word_and_traditional_form(dictionary):
    simp = dictionary.lookup("学习")[0]
    assert (simp.headword, simp.alt_form, simp.reading) == ("学习", "學習", "xué xí")
    assert simp.definitions == ("to study; to learn",)
    # A traditional-only spelling finds the same entry, with the simplified headword.
    assert dictionary.lookup("學習")[0].headword == "学习"
    assert dictionary.lookup("臺灣")[0].headword == "台湾"


def test_polyphone_returns_every_reading(dictionary):
    assert [e.reading for e in dictionary.lookup("行")] == ["xíng", "háng"]


def test_chengyu_and_saying_with_comma(dictionary):
    assert dictionary.lookup("一举两得")[0].kind == "chengyu"
    saying = dictionary.lookup(SAYING)[0]
    assert saying.kind == "suyu"
    # Punctuation in the query does not matter, with either comma.
    assert dictionary.lookup("三个臭皮匠,顶个诸葛亮")[0] == saying


def test_unknown_word_and_empty_query(dictionary):
    assert dictionary.lookup("没有这个词") == []
    assert dictionary.lookup("") == []


def test_download_failure_is_reported_not_raised(tmp_path):
    def broken():
        raise OSError("no route to host")

    d = Dictionary(tmp_path, fetch=broken, min_entries=1)
    d.start()
    assert d.wait(10) is False
    assert d.status()["state"] == "unavailable"
    assert "definitions are off" in d.status()["error"]
    assert d.lookup("学习") == []


def test_bad_download_is_rejected(tmp_path):
    d = Dictionary(tmp_path, fetch=lambda: gzip.compress(b"<html>not found</html>"))
    d.start()
    assert d.wait(10) is False
    assert not d.path.exists()


def test_cached_database_is_reused_without_downloading(tmp_path, mini_gz):
    first = Dictionary(tmp_path, fetch=lambda: mini_gz, min_entries=1)
    first.start()
    assert first.wait(10)

    def forbidden():
        raise AssertionError("should not download again")

    second = Dictionary(tmp_path, fetch=forbidden, min_entries=1)
    second.start()
    assert second.wait(10)
    assert second.lookup("学习")


def test_corrupt_cache_is_rebuilt(tmp_path, mini_gz):
    path = tmp_path / "cedict" / "cedict.sqlite"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"this is not a database")
    d = Dictionary(tmp_path, fetch=lambda: mini_gz, min_entries=1)
    d.start()
    assert d.wait(10)
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM entries").fetchone()[0] == 14


def test_stale_cache_survives_failed_refresh(tmp_path, mini_gz, monkeypatch):
    first = Dictionary(tmp_path, fetch=lambda: mini_gz, min_entries=1)
    first.start()
    assert first.wait(10)
    monkeypatch.setattr(cedict, "REFRESH_AFTER_SECONDS", -1)  # everything counts as stale

    def offline():
        raise OSError("offline")

    again = Dictionary(tmp_path, fetch=offline, min_entries=1)
    again.start()
    assert again.wait(10)
    assert again.lookup("学习")


def test_expressions_find_saying_with_punctuation(zh):
    text = f"他说{SAYING}。"
    spans = zh.expressions(text)
    assert [s.text for s in spans] == [SAYING]
    span = spans[0]
    assert text[span.start : span.end] == SAYING
    assert span.entries[0].kind == "suyu"


def test_expressions_ignore_single_tokens(zh):
    # With CEDICT words fed to jieba, 学习 and 一举两得 are single tokens, so not "expressions".
    assert zh.segment("我们学习一举两得") == ["我们", "学习", "一举两得"]
    assert zh.expressions("我们学习一举两得") == []


def test_jieba_keeps_idiom_whole(zh):
    assert "一举两得" in zh.segment("这样做一举两得。")
    assert "".join(zh.segment(SAYING)) == SAYING


def test_expressions_off_without_dictionary():
    lang = Chinese()
    assert lang.expressions("一举两得") == []
    assert lang.lookup("学习") == []
    assert lang.dictionary_status()["state"] == "none"


def test_feeding_jieba_keeps_its_own_word_frequencies(dictionary):
    """Regression: with a fresh jieba, FREQ is empty until it initializes, and an early
    snapshot of it made every CEDICT word overwrite jieba's real frequency with 1."""
    import jieba

    tokenizer = jieba.Tokenizer()  # not initialized yet
    Chinese._feed_jieba(tokenizer, dictionary)
    assert tokenizer.FREQ["现在"] > 1000  # jieba's own frequency, untouched
    assert tokenizer.FREQ["一举两得"] > 3  # jieba knows this idiom already, so it was not touched
    assert list(tokenizer.cut("那你现在去吃吧。")) == ["那", "你", "现在", "去", "吃", "吧", "。"]
    assert "一举两得" in list(tokenizer.cut("这样做一举两得。"))


def test_expressions_skip_accidental_two_character_matches(zh):
    # "多大" is a headword, but across a comma or as two separate words it is not an expression.
    assert zh.expressions("多，大") == []
    assert zh.expressions("去，火") == []


def test_expressions_report_three_character_phrases_jieba_splits(zh, monkeypatch):
    monkeypatch.setattr(zh, "segment", lambda text: list(text))  # pretend jieba split everything
    assert [s.text for s in zh.expressions("他说不客气")] == ["不客气"]
    assert zh.expressions("去火") == []  # only two characters, and a plain word
