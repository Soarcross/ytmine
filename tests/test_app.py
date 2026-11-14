"""End-to-end tests of the Flask API with the network and ffmpeg faked out."""

import pytest

from ytmine.app import Services, create_app
from ytmine.config import Settings
from ytmine.fetch import FetchError, VideoData

ZH = """WEBVTT

00:00:02.000 --> 00:00:04.000
我喜欢学习中文。

00:00:05.000 --> 00:00:07.000
今天天气很好。
"""
EN = """WEBVTT

00:00:02.000 --> 00:00:04.000
I like studying Chinese.
"""


def fake_fetch(url, cache_dir):
    if "bad" in url:
        raise FetchError("yt-dlp could not fetch this video: boom", "pip install -U yt-dlp")
    return VideoData("abcdefghijk", "Test video", 60.0, ZH, "manual (zh-Hans)", EN, "manual (en)")


cut_calls = []


def fake_cut(ffmpeg, source, dest, start, length):
    dest.write_bytes(b"fake mp3")
    cut_calls.append((start, length))


@pytest.fixture
def client(tmp_path):
    cut_calls.clear()
    audio = tmp_path / "audio.webm"
    audio.write_bytes(b"x")
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        ankiconnect_url="http://127.0.0.1:1",  # nothing listens here
    )
    services = Services(
        fetch_video=fake_fetch,
        download_audio=lambda vid, cache: audio,
        cut_clip=fake_cut,
        find_ffmpeg=lambda: "ffmpeg",
    )
    return create_app(settings, services).test_client()


def load(client):
    return client.post("/api/load", json={"url": "https://youtu.be/abcdefghijk"}).get_json()


def test_load_returns_lines_with_tokens_and_english(client):
    data = load(client)
    assert data["title"] == "Test video"
    assert [line["text"] for line in data["lines"]] == ["我喜欢学习中文。", "今天天气很好。"]
    assert data["lines"][0]["english"] == "I like studying Chinese."
    assert "".join(t[0] for t in data["lines"][0]["tokens"]) == "我喜欢学习中文。"


def test_load_error_is_reported_with_hint(client):
    response = client.post("/api/load", json={"url": "bad"})
    assert response.status_code == 502
    assert "pip install -U yt-dlp" in response.get_json()["hint"]


def test_mine_creates_card_with_clip_and_pinyin(client):
    load(client)
    response = client.post("/api/mine", json={"line_index": 0})
    assert response.status_code == 201
    card = response.get_json()
    assert card["sentence"] == "我喜欢学习中文。"
    assert card["pinyin"].startswith("wǒ xǐ huān")
    assert card["english"] == "I like studying Chinese."
    assert cut_calls[0] == pytest.approx((1.7, 2.6))
    assert client.get(card["audio_url"]).data == b"fake mp3"


def test_mining_the_same_sentence_twice_is_rejected(client):
    load(client)
    client.post("/api/mine", json={"line_index": 0})
    again = client.post("/api/mine", json={"line_index": 0})
    assert again.status_code == 409 and again.get_json()["duplicate"] is True


def test_cloze_target_is_validated_and_stored(client):
    data = load(client)
    tokens = data["lines"][0]["tokens"]
    ti = next(i for i, t in enumerate(tokens) if t[0] == "学习")
    card = client.post(
        "/api/mine", json={"line_index": 0, "target_index": ti, "target_text": "学习"}
    ).get_json()
    assert card["target"] == "学习"
    assert card["parts"] == {"before": "我喜欢", "target": "学习", "after": "中文。"}


def test_wrong_target_is_rejected(client):
    load(client)
    response = client.post(
        "/api/mine", json={"line_index": 0, "target_index": 0, "target_text": "不是"}
    )
    assert response.status_code == 400


def test_edit_and_delete_card(client):
    load(client)
    card = client.post("/api/mine", json={"line_index": 0}).get_json()
    edited = client.patch(
        f"/api/cards/{card['id']}", json={"english": "I enjoy Chinese.", "target": "中文"}
    ).get_json()
    assert edited["english"] == "I enjoy Chinese."
    assert edited["parts"]["before"] == "我喜欢学习"
    assert client.delete(f"/api/cards/{card['id']}").status_code == 200
    assert client.get("/api/cards").get_json()["cards"] == []
    # Deleting frees the sentence for mining again.
    assert client.post("/api/mine", json={"line_index": 0}).status_code == 201


def test_export_apkg_downloads_a_zip(client):
    load(client)
    client.post("/api/mine", json={"line_index": 0})
    response = client.get("/api/export.apkg")
    assert response.status_code == 200
    assert response.data[:2] == b"PK"


def test_export_with_no_cards_explains_itself(client):
    assert client.get("/api/export.apkg").status_code == 400


def test_send_to_anki_when_anki_is_closed(client):
    load(client)
    client.post("/api/mine", json={"line_index": 0})
    response = client.post("/api/anki/send", json={})
    assert response.status_code == 502
    assert "Open Anki" in response.get_json()["error"]


def test_requests_from_other_hosts_are_refused(client):
    response = client.get("/api/info", headers={"Host": "evil.example"})
    assert response.status_code == 403


def test_cross_site_posts_are_refused(client):
    response = client.post(
        "/api/load", json={"url": "x"}, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403


def test_index_page_is_served(client):
    response = client.get("/")
    assert response.status_code == 200 and b"ytmine" in response.data
    response.close()


def test_mine_a_span_of_lines_as_one_card(client):
    load(client)
    response = client.post(
        "/api/mine",
        json={
            "line_index": 0,
            "line_end": 1,
            "target_line": 1,
            "target_index": 1,
            "target_text": "很",
        },
    )
    assert response.status_code == 201
    card = response.get_json()
    assert card["sentence"] == "我喜欢学习中文。今天天气很好。"
    assert card["id"] == "abcdefghijk_2000_7000"  # differs from the single-line card's id
    assert card["parts"] == {"before": "我喜欢学习中文。今天天气", "target": "很", "after": "好。"}
    assert cut_calls[0] == pytest.approx((1.7, 5.6))
    # The single first line is a different sentence, so it can still be mined.
    assert client.post("/api/mine", json={"line_index": 0}).status_code == 201


def test_span_target_must_be_inside_the_span(client):
    load(client)
    response = client.post(
        "/api/mine",
        json={
            "line_index": 0,
            "line_end": 0,
            "target_line": 1,
            "target_index": 1,
            "target_text": "天气",
        },
    )
    assert response.status_code == 400


def test_bad_span_is_rejected(client):
    load(client)
    assert client.post("/api/mine", json={"line_index": 1, "line_end": 0}).status_code == 400
    assert client.post("/api/mine", json={"line_index": 0, "line_end": 5}).status_code == 400


def test_full_audio_is_served_for_the_fallback_player(client):
    assert client.get("/api/audio").status_code == 404  # no video yet
    load(client)
    client.post("/api/mine", json={"line_index": 0})  # waits for the audio download
    response = client.get("/api/audio")
    assert response.status_code == 200
    assert response.data == b"x"


def test_load_reports_punctuated_subtitles(client):
    assert load(client)["unpunctuated"] is False


def test_lines_of_a_multi_line_card_show_as_mined_after_reload(client):
    load(client)
    client.post("/api/mine", json={"line_index": 0, "line_end": 1})
    assert [line["mined"] for line in load(client)["lines"]] == [True, True]


def test_overlay_script_is_served_and_the_page_loads_it_before_app_js(client):
    page = client.get("/").get_data(as_text=True)
    assert page.index("/static/overlay.js") < page.index("/static/app.js")
    assert 'id="stage"' in page and 'id="overlay-root"' in page
    assert client.get("/static/overlay.js").status_code == 200
