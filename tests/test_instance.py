"""Running one ytmine at a time: finding it, opening it again, stopping it."""

import io
import json
import sys
import threading

import pytest
from werkzeug.serving import make_server

from ytmine import cli, instance
from ytmine.app import Services, create_app
from ytmine.config import Settings
from ytmine.pairing import TOKEN_HEADER


@pytest.fixture(autouse=True)
def scratch_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("YTMINE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("YTMINE_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("YTMINE_CONFIG_DIR", str(tmp_path / "config"))


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        token_file=tmp_path / "config" / "pairing_token",
    )
    return create_app(settings, Services(find_ffmpeg=lambda: None))


@pytest.fixture
def live(app):
    """A real ytmine server on a free port, wired for /api/shutdown as the CLI does it."""
    server = make_server(instance.HOST, 0, app, threaded=True)
    app.config["SHUTDOWN"] = lambda: threading.Thread(target=server.shutdown, daemon=True).start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


# -- /api/shutdown ------------------------------------------------------------------------


def test_shutdown_needs_the_token(app):
    client = app.test_client()
    app.config["SHUTDOWN"] = lambda: None
    assert client.post("/api/shutdown").status_code == 403
    assert client.post("/api/shutdown", headers={TOKEN_HEADER: "x" * 40}).status_code == 403
    # Not even our own page can stop the server without it.
    page = {"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}
    assert client.post("/api/shutdown", headers=page).status_code == 403


def test_shutdown_with_the_token_stops_the_server(app):
    stopped = threading.Event()
    app.config["SHUTDOWN"] = stopped.set
    response = app.test_client().post(
        "/api/shutdown", headers={TOKEN_HEADER: app.config["PAIRING_TOKEN"]}
    )
    assert response.status_code == 200
    assert stopped.wait(2)


def test_shutdown_without_a_hook_says_so(app):
    response = app.test_client().post(
        "/api/shutdown", headers={TOKEN_HEADER: app.config["PAIRING_TOKEN"]}
    )
    assert response.status_code == 503


# -- finding the running server -----------------------------------------------------------


def test_a_running_server_is_found_from_its_note(live):
    port = live.server_port
    assert instance.find_running(port) == port  # by probing
    instance.record(port)
    assert instance.find_running(1) == port  # by the note, whatever port is asked for
    instance.forget()
    assert not instance.run_file().exists()


def test_a_note_left_by_a_crash_is_ignored(free_port):
    instance.run_file().parent.mkdir(parents=True, exist_ok=True)
    instance.run_file().write_text(json.dumps({"port": free_port, "pid": 1}), encoding="utf-8")
    assert instance.find_running(free_port) is None
    instance.forget()  # not ours, so it stays
    assert instance.run_file().exists()


def test_something_else_on_the_port_is_not_ytmine(free_port):
    from flask import Flask

    other = Flask("other")
    other.add_url_rule("/api/info", "info", lambda: {"hello": "world"})
    server = make_server(instance.HOST, free_port, other, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert not instance.is_ytmine(free_port)
    finally:
        server.shutdown()
        server.server_close()


def test_request_stop_stops_a_live_server(live, app):
    port = live.server_port
    assert instance.request_stop(port, "x" * 40) is False  # wrong token
    assert instance.request_stop(port, app.config["PAIRING_TOKEN"]) is True
    for _ in range(50):
        if not instance.is_ytmine(port, timeout=0.2):
            break
        threading.Event().wait(0.1)
    assert not instance.is_ytmine(port, timeout=0.2)


@pytest.fixture
def free_port():
    import socket

    with socket.socket() as sock:
        sock.bind((instance.HOST, 0))
        return sock.getsockname()[1]


# -- the command line ---------------------------------------------------------------------


def test_starting_again_opens_the_running_one(live, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr(cli, "find_ffmpeg", lambda: pytest.fail("must not start a second server"))
    port = live.server_port
    assert cli.main(["--port", str(port)]) == 0
    assert opened == [f"http://127.0.0.1:{port}/"]
    assert "already running" in capsys.readouterr().out


def test_stop_command(live, app, monkeypatch, capsys):
    port = live.server_port
    # The CLI reads the token from the config dir, as the real one does.
    monkeypatch.setattr(cli, "Settings", _settings_with_token(app))
    assert cli.main(["--stop", "--port", str(port)]) == 0
    assert "Stopped ytmine" in capsys.readouterr().out


def test_stop_when_nothing_runs(free_port, capsys):
    assert cli.main(["--stop", "--port", str(free_port)]) == 0
    assert "not running" in capsys.readouterr().out


def _settings_with_token(app):
    class WithToken(Settings):
        @classmethod
        def load(cls):
            settings = Settings.load()
            settings.token_file.parent.mkdir(parents=True, exist_ok=True)
            settings.token_file.write_text(app.config["PAIRING_TOKEN"], encoding="utf-8")
            return settings

    return WithToken


def test_background_mode_writes_to_a_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    assert cli.main(["--background", "--show-token"]) == 0
    sys.stdout.flush()
    log = tmp_path / "cache" / "ytmine.log"
    token = (tmp_path / "config" / "pairing_token").read_text(encoding="utf-8").strip()
    assert token in log.read_text(encoding="utf-8")
    sys.stdout.close()


def test_no_console_at_all_also_logs(tmp_path, monkeypatch):
    # pythonw on Windows: there is no stdout to print to.
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert cli.main(["--show-token"]) == 0
    assert (tmp_path / "cache" / "ytmine.log").exists()
    sys.stdout.close()


def test_a_big_log_is_set_aside(tmp_path, monkeypatch):
    log = tmp_path / "cache" / "ytmine.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"x" * (cli.LOG_LIMIT + 1))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    cli.main(["--background", "--show-token"])
    sys.stdout.close()
    assert (tmp_path / "cache" / "ytmine.old.log").stat().st_size > cli.LOG_LIMIT
    assert log.stat().st_size < 1000
