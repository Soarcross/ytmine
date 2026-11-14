from ytmine.lang import get_language
from ytmine.subtitles import (
    Cue,
    Line,
    align_translation,
    clean_cues,
    is_unpunctuated,
    join_span,
    line_index_at,
    parse_vtt,
)


def texts(lines):
    return [line.text for line in lines]


def test_parse_vtt_reads_times_and_skips_header(fixture_text):
    cues = parse_vtt(fixture_text("manual_zh.vtt"))
    assert len(cues) == 6
    assert cues[1].start == 2.5 and cues[1].end == 4.0
    assert cues[1].text == "你好，我叫小明，"


def test_parse_vtt_accepts_short_timestamps_and_commas():
    cues = parse_vtt("WEBVTT\n\n01:02.500 --> 01:04,000\n你好\n")
    assert [(c.start, c.end) for c in cues] == [(62.5, 64.0)]


def test_rolling_auto_captions_are_deduplicated(fixture_text):
    lines = clean_cues(parse_vtt(fixture_text("rolling_auto.vtt")), merge=False)
    assert texts(lines) == ["大家好今天我们", "去市场买菜", "天气很好。"]
    # Tiny repeat cues extend the previous line instead of making new ones.
    assert lines[0].start == 1.0 and lines[0].end == 3.01
    assert lines[1].start == 3.01 and lines[1].end == 5.01


def test_inline_tags_and_entities_are_stripped():
    cues = [Cue(0, 2, "<c.yellow>你好</c>&amp;<00:00:01.000>再见&nbsp;吧")]
    assert texts(clean_cues(cues)) == ["你好&再见 吧"]


def test_sound_cues_are_dropped(fixture_text):
    lines = clean_cues(parse_vtt(fixture_text("manual_zh.vtt")))
    assert all("音乐" not in line.text for line in lines)


def test_fragments_split_mid_sentence_are_merged(fixture_text):
    lines = clean_cues(parse_vtt(fixture_text("manual_zh.vtt")))
    assert texts(lines) == ["你好，我叫小明，很高兴认识你。", "我昨天去了超市买东西。", "你呢？"]
    assert (lines[0].start, lines[0].end) == (2.5, 5.5)
    assert (lines[1].start, lines[1].end) == (6.0, 8.5)


def test_complete_sentences_are_not_merged():
    cues = [Cue(0, 2, "我今天很高兴。"), Cue(2.1, 4, "我们去公园玩吧。")]
    assert len(clean_cues(cues)) == 2


def test_distant_fragments_are_not_merged():
    cues = [Cue(0, 1, "我昨天去了"), Cue(10, 12, "超市买东西")]
    assert len(clean_cues(cues)) == 2


def test_repeated_line_after_a_pause_is_kept():
    cues = [Cue(0, 1, "好的。"), Cue(5, 6, "好的。")]
    assert texts(clean_cues(cues)) == ["好的。", "好的。"]


def test_overlapping_end_times_are_trimmed():
    cues = [Cue(0, 5, "第一句话说完了。"), Cue(3, 6, "第二句话也说完了。")]
    lines = clean_cues(cues)
    assert lines[0].end == 3


def test_english_fragments_join_with_a_space():
    cues = [Cue(0, 1, "Hello,"), Cue(1.1, 2, "world.")]
    assert texts(clean_cues(cues)) == ["Hello, world."]


def make_lines():
    return [Line(1.0, 3.0, "a"), Line(3.5, 5.0, "b"), Line(8.0, 9.0, "c")]


def test_line_index_at_basic_lookup():
    lines = make_lines()
    assert line_index_at(lines, 0.5) == -1  # before the first line
    assert line_index_at(lines, 1.0) == 0  # exactly at a start
    assert line_index_at(lines, 2.9) == 0
    assert line_index_at(lines, 3.5) == 1
    assert line_index_at(lines, 100) == 2  # after the last line


def test_line_index_at_sticks_through_gaps_by_default():
    assert line_index_at(make_lines(), 6.5) == 1


def test_line_index_at_hold_drops_highlight_in_gaps():
    lines = make_lines()
    assert line_index_at(lines, 5.2, hold=0.5) == 1
    assert line_index_at(lines, 6.5, hold=0.5) == -1


def test_align_translation_matches_by_midpoint(fixture_text):
    lines = clean_cues(parse_vtt(fixture_text("manual_zh.vtt")))
    english = clean_cues(parse_vtt(fixture_text("english.vtt")), merge=False)
    align_translation(lines, english)
    assert [line.english for line in lines] == [
        "Hello, my name is Xiaoming, nice to meet you.",
        "I went to the supermarket yesterday.",
        "And you?",
    ]


def test_align_translation_drops_cues_far_from_any_line():
    lines = [Line(10, 12, "你好。")]
    align_translation(lines, [Line(0, 1, "far away")])
    assert lines[0].english == ""


ZH_CONT = get_language("zh").continues_after
ZH_START = get_language("zh").starts_sentence


def clean_unpunctuated(cues):
    return clean_cues(cues, continues_after=ZH_CONT, starts_sentence=ZH_START)


def test_unpunctuated_captions_are_detected():
    assert is_unpunctuated([Line(0, 1, "我想要来跟大家分享一个"), Line(1, 2, "观点")])
    assert not is_unpunctuated([Line(0, 1, "你好。"), Line(1, 2, "再见！")])


def test_unpunctuated_fragments_are_rebuilt_into_sentences():
    cues = [
        Cue(6.9, 10.4, "在今天这期视频播客当中"),
        Cue(10.5, 13.8, "我想要来跟大家分享一个"),
        Cue(13.9, 17.9, "和语言学习有关的观点"),
        Cue(18.2, 19.6, "这个观点是这样的"),
    ]
    lines = clean_unpunctuated(cues)
    # "一个" cannot end a clause, so no comma is added there.
    assert "我想要来跟大家分享一个和语言学习有关的观点" in lines[0].text
    assert lines[-1].text == "这个观点是这样的"


def test_unpunctuated_rebuild_stops_at_sentence_openers():
    cues = [Cue(0, 1.5, "我很累"), Cue(1.6, 3, "但是我很开心")]
    assert texts(clean_unpunctuated(cues)) == ["我很累", "但是我很开心"]


def test_unpunctuated_rebuild_respects_length_limit():
    cues = [Cue(i * 2.0, i * 2.0 + 1.9, "我觉得这个东西特别的") for i in range(10)]
    assert all(len(line.text) <= 40 for line in clean_unpunctuated(cues))


def test_join_span_adds_commas_only_where_needed():
    assert join_span(["我想分享一个", "观点"]) == ("我想分享一个，观点", [0, 7])
    assert join_span(["你好。", "再见"]) == ("你好。再见", [0, 3])
    assert join_span(["Hello", "world"]) == ("Hello world", [0, 6])


def test_sentence_opener_wins_over_a_dangling_ending():
    cues = [Cue(0, 3, "我买的机票是早上9点多的"), Cue(3, 5, "所以我得一大早就出门")]
    assert texts(clean_unpunctuated(cues)) == ["我买的机票是早上9点多的", "所以我得一大早就出门"]
