"""Who may talk to the server: its own page, the paired extension, and nobody else."""

import pytest

from ytmine.app import Services, create_app
from ytmine.config import Settings
from ytmine.pairing import TOKEN_HEADER, is_extension_origin, load_or_create_token

EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
MOZ = "moz-extension://12345678-1234-1234-1234-123456789abc"


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data", cache_dir=tmp_path / "cache", token_file=tmp_path / "token"
    )
    return create_app(settings, Services(find_ffmpeg=lambda: None))


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def token(app):
    return app.config["PAIRING_TOKEN"]


def get(client, origin=None, token=None, path="/api/info", **kw):
    headers = {}
    if origin:
        headers["Origin"] = origin
    if token:
        headers[TOKEN_HEADER] = token
    headers.update(kw.pop("headers", {}))
    return client.open(path, headers=headers, **kw)


# -- unchanged rules ----------------------------------------------------------------------


def test_own_page_and_direct_requests_still_work(client):
    assert get(client).status_code == 200  # no Origin
    assert get(client, origin="http://localhost").status_code == 200  # same host as the request


def test_other_hosts_are_still_refused(client, token):
    assert get(client, headers={"Host": "evil.example"}).status_code == 403
    # Even a paired extension cannot get past the Host check (DNS rebinding).
    response = get(client, origin=EXT, token=token, headers={"Host": "evil.example"})
    assert response.status_code == 403


# -- web pages never get in ---------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        "https://www.youtube.com",
        "https://youtube.com",
        "https://evil.example",
        "http://127.0.0.1:9999",  # another local site
        "null",
        "file://",
        "chrome-extension://",  # no extension id
        "https://x.chrome-extension://abc",
    ],
)
def test_web_origins_are_refused_with_and_without_the_token(client, token, origin):
    assert get(client, origin=origin).status_code == 403
    assert get(client, origin=origin, token=token).status_code == 403


def test_youtube_cannot_post_either(client, token):
    response = client.post(
        "/api/load",
        json={"url": "x"},
        headers={"Origin": "https://www.youtube.com", TOKEN_HEADER: token},
    )
    assert response.status_code == 403
    assert "paired browser extension" in response.get_json()["error"]


def test_a_refused_origin_gets_no_cors_headers(client, token):
    response = get(client, origin="https://www.youtube.com", token=token)
    assert "Access-Control-Allow-Origin" not in response.headers


# -- the paired extension -------------------------------------------------------------------


@pytest.mark.parametrize("origin", [EXT, MOZ])
def test_extension_with_the_token_is_accepted(client, token, origin):
    response = get(client, origin=origin, token=token)
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert TOKEN_HEADER in response.headers["Access-Control-Allow-Headers"]


@pytest.mark.parametrize("origin", [EXT, MOZ])
def test_extension_without_or_with_the_wrong_token_is_refused(client, token, origin):
    assert get(client, origin=origin).status_code == 403
    assert get(client, origin=origin, token="wrong").status_code == 403
    assert get(client, origin=origin, token=token[:-1]).status_code == 403
    assert get(client, origin=origin, token=token + "x").status_code == 403
    assert get(client, origin=origin, token="").status_code == 403


def test_extension_can_use_post_patch_and_delete(client, token):
    headers = {"Origin": EXT, TOKEN_HEADER: token}
    assert (
        client.post("/api/load", json={}, headers=headers).status_code == 400
    )  # reached the route
    assert client.put("/api/settings", json={}, headers=headers).status_code == 200
    assert client.delete("/api/cards/nope", headers=headers).status_code == 404


def test_preflight_from_an_extension_gets_headers_but_no_data(client):
    response = client.options(
        "/api/mine",
        headers={
            "Origin": EXT,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": f"content-type, {TOKEN_HEADER.lower()}",
        },
    )
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Origin"] == EXT
    assert response.data == b""


def test_preflight_from_a_web_page_is_refused(client):
    response = client.options(
        "/api/mine",
        headers={"Origin": "https://www.youtube.com", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 403


def test_the_token_is_not_in_any_response(client, token):
    for path in ("/api/info", "/api/settings", "/", "/static/app.js"):
        assert token.encode() not in get(client, origin=EXT, token=token, path=path).data


# -- the token itself ---------------------------------------------------------------------


def test_is_extension_origin():
    assert is_extension_origin(EXT) and is_extension_origin(MOZ)
    assert not is_extension_origin("chrome-extension://")
    assert not is_extension_origin("https://chrome-extension://x")
    assert not is_extension_origin("")


def test_token_is_random_saved_and_reused(tmp_path):
    path = tmp_path / "config" / "pairing_token"
    first = load_or_create_token(path)
    assert len(first) >= 40 and path.read_text(encoding="utf-8").strip() == first
    assert load_or_create_token(path) == first  # survives a restart
    assert load_or_create_token(tmp_path / "other") != first  # not a constant


def test_a_damaged_token_file_is_replaced(tmp_path):
    path = tmp_path / "pairing_token"
    path.write_text("short", encoding="utf-8")
    fresh = load_or_create_token(path)
    assert fresh != "short" and path.read_text(encoding="utf-8").strip() == fresh


def test_token_without_a_file_lives_in_memory_and_differs_per_app(tmp_path):
    def make(n):
        settings = Settings(data_dir=tmp_path / f"d{n}", cache_dir=tmp_path / f"c{n}")
        return create_app(settings, Services(find_ffmpeg=lambda: None))

    assert make(1).config["PAIRING_TOKEN"] != make(2).config["PAIRING_TOKEN"]


def test_same_settings_file_gives_the_same_token_after_restart(tmp_path):
    def make():
        settings = Settings(
            data_dir=tmp_path / "d", cache_dir=tmp_path / "c", token_file=tmp_path / "tok"
        )
        return create_app(settings, Services(find_ffmpeg=lambda: None))

    assert make().config["PAIRING_TOKEN"] == make().config["PAIRING_TOKEN"]


def test_show_token_prints_it_and_exits(tmp_path, monkeypatch, capsys):
    from ytmine import cli

    monkeypatch.setenv("YTMINE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("YTMINE_CACHE_DIR", str(tmp_path / "c"))
    monkeypatch.setattr("ytmine.config.config_path", lambda: tmp_path / "cfg" / "config.json")
    assert cli.main(["--show-token"]) == 0
    printed = capsys.readouterr().out.strip()
    assert printed == (tmp_path / "cfg" / "pairing_token").read_text(encoding="utf-8").strip()


def test_config_dir_can_be_overridden_for_tests_and_portable_installs(tmp_path, monkeypatch):
    from ytmine.config import Settings, config_path

    monkeypatch.setenv("YTMINE_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("YTMINE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("YTMINE_CACHE_DIR", str(tmp_path / "c"))
    assert config_path() == tmp_path / "cfg" / "config.json"
    assert Settings.load().token_file == tmp_path / "cfg" / "pairing_token"


# -- requests with no Origin header: what Chrome sends for an extension's GETs -----------------


def sec(site=None, mode=None, token=None):
    headers = {}
    if site:
        headers["Sec-Fetch-Site"] = site
    if mode:
        headers["Sec-Fetch-Mode"] = mode
    if token:
        headers[TOKEN_HEADER] = token
    return headers


@pytest.mark.parametrize(
    "headers",
    [
        {},  # curl, scripts, tests: no Sec-Fetch headers at all
        {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"},  # typing the address
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"},  # following a link here
        {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"},  # our own page
        {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "no-cors"},  # our own <audio>
    ],
)
def test_ordinary_origin_less_requests_still_work(client, headers):
    assert get(client, headers=headers).status_code == 200


@pytest.mark.parametrize(
    "headers",
    [
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},  # <img src=...> on a site
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors"},
        {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "no-cors"},  # another local port
        {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors"},  # an extension's GET, unpaired
    ],
)
def test_origin_less_requests_from_other_contexts_need_the_token(client, token, headers):
    assert get(client, headers=headers).status_code == 403
    assert get(client, headers={**headers, TOKEN_HEADER: token}).status_code == 200


def test_chromes_extension_get_has_no_origin_but_must_carry_the_token(client, token):
    chrome_extension_get = {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors"}
    assert get(client, headers=chrome_extension_get).status_code == 403
    assert get(client, headers={**chrome_extension_get, TOKEN_HEADER: "wrong"}).status_code == 403
    assert get(client, headers={**chrome_extension_get, TOKEN_HEADER: token}).status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"}])
def test_a_wrong_token_is_refused_even_with_no_origin(client, headers):
    assert get(client, headers={**headers, TOKEN_HEADER: "wrong"}).status_code == 403


# -- handing the token to our own page ----------------------------------------------------


SAME_PAGE = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}


def test_our_own_page_can_read_the_token(client, token):
    response = get(client, path="/api/pairing", headers=SAME_PAGE)
    assert response.status_code == 200
    assert response.get_json() == {"token": token}
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "headers",
    [
        {},  # header-less client: it can read the token file instead
        {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "navigate"},  # typed into the address bar
        {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "navigate"},
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"},  # a link from a site
        {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "no-cors"},  # <script src>
        {"Sec-Fetch-Site": "same-site", "Sec-Fetch-Mode": "cors"},  # another localhost port
        {"Sec-Fetch-Site": "none", "Sec-Fetch-Mode": "cors"},  # an extension's GET
    ],
)
def test_nobody_else_can_read_the_token(client, headers):
    assert get(client, path="/api/pairing", headers=headers).status_code == 403


def test_web_pages_and_extensions_cannot_read_the_token(client, token):
    page = {"Origin": "https://www.youtube.com", **SAME_PAGE}
    assert get(client, path="/api/pairing", headers=page).status_code == 403
    # Even the paired extension gets nothing here: it already has the token.
    assert get(client, origin=EXT, token=token, path="/api/pairing").status_code == 403
    rebound = {"Host": "evil.example", **SAME_PAGE}  # DNS rebinding
    assert get(client, path="/api/pairing", headers=rebound).status_code == 403
