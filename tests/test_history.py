import json

from ytmine.history import History, sentence_key


def card(card_id="v_1000", sentence="你好，世界！", session="s1"):
    return {"id": card_id, "sentence": sentence, "session_id": session}


def test_sentence_key_ignores_spacing_punctuation_and_case():
    assert sentence_key("你好， 世界！") == sentence_key("你好世界")
    assert sentence_key("Hello, World") == sentence_key("hello world")


def test_add_and_detect_duplicates(tmp_path):
    history = History(tmp_path / "mined_history.json")
    history.add(card())
    assert history.has_sentence("你好 世界")
    assert not history.has_sentence("再见")


def test_history_persists_between_runs(tmp_path):
    path = tmp_path / "mined_history.json"
    History(path).add(card())
    assert History(path).get("v_1000")["sentence"] == "你好，世界！"
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


def test_delete_lets_a_sentence_be_mined_again(tmp_path):
    history = History(tmp_path / "h.json")
    history.add(card())
    history.delete("v_1000")
    assert not history.has_sentence("你好世界")


def test_cards_can_be_filtered_by_session(tmp_path):
    history = History(tmp_path / "h.json")
    history.add(card("a", "一", "s1"))
    history.add(card("b", "二", "s2"))
    assert [c["id"] for c in history.cards("s1")] == ["a"]
    assert len(history.cards()) == 2


def test_update_changes_fields(tmp_path):
    history = History(tmp_path / "h.json")
    history.add(card())
    history.update("v_1000", {"english": "hi"})
    assert history.get("v_1000")["english"] == "hi"


def test_corrupt_file_is_set_aside(tmp_path):
    path = tmp_path / "h.json"
    path.write_text("{not json", encoding="utf-8")
    history = History(path)
    assert history.cards() == []
    assert (tmp_path / "h.json.corrupt").exists()


def test_add_and_update_stamp_modified_at(tmp_path):
    history = History(tmp_path / "h.json")
    history.add(card())
    first = history.get("v_1000")["modified_at"]
    updated = history.update("v_1000", {"english": "hello"})
    assert updated["modified_at"] >= first


def test_duplicate_index_follows_edits_and_deletes(tmp_path):
    history = History(tmp_path / "h.json")
    history.add(card(sentence="我很忙"))
    history.update("v_1000", {"sentence": "我不忙"})
    assert not history.has_sentence("我很忙")
    assert history.has_sentence("我不忙。")
    history.delete("v_1000")
    assert not history.has_sentence("我不忙")
