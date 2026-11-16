"""The native messaging host that lets the browser extension start ytmine."""

import base64
import hashlib
import io
import json
import struct
import sys
from pathlib import Path

import pytest

from ytmine import cli, launcher, nativehost
from ytmine.launcher import Places

EXT = Path(__file__).resolve().parent.parent / "extension"
SPACEY_PYTHON = "/opt/my tools/bin/python3"


@pytest.fixture(autouse=True)
def config_dir(tmp_path, monkeypatch):
    for name in ("DATA", "CACHE", "CONFIG"):
        monkeypatch.setenv(f"YTMINE_{name}_DIR", str(tmp_path / name.lower()))
    return tmp_path / "config"


def framed(message) -> io.BytesIO:
    data = json.dumps(message).encode()
    return io.BytesIO(struct.pack("=I", len(data)) + data)


# -- the extension and the host agree -----------------------------------------------------


def test_the_manifest_key_gives_the_id_the_host_allows():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    digest = hashlib.sha256(base64.b64decode(manifest["key"])).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(c, 16)) for c in digest)
    assert extension_id == nativehost.CHROME_EXTENSION_ID
    gecko = manifest["browser_specific_settings"]["gecko"]["id"]
    assert gecko == nativehost.FIREFOX_EXTENSION_ID


def test_the_worker_asks_the_host_by_its_registered_name():
    worker = (EXT / "background.js").read_text(encoding="utf-8")
    assert f'NATIVE_HOST = "{nativehost.HOST_NAME}"' in worker
    assert "onStartup" in worker


# -- the protocol ---------------------------------------------------------------------------


def test_messages_round_trip():
    out = io.BytesIO()
    nativehost.write_message(out, {"ok": True, "port": 8787})
    out.seek(0)
    assert nativehost.read_message(out) == {"ok": True, "port": 8787}


@pytest.mark.parametrize(
    "raw",
    [
        b"",
        b"\x01\x00",
        struct.pack("=I", 10**8) + b"{}",
        struct.pack("=I", 2) + b"[]",
        struct.pack("=I", 3) + b"{x}",
    ],
)
def test_bad_messages_are_ignored(raw):
    assert nativehost.read_message(io.BytesIO(raw)) is None


def test_unknown_commands_are_refused():
    assert nativehost.handle({"cmd": "rm -rf"}) == {"ok": False, "error": "unknown-command"}
    assert nativehost.handle(None)["ok"] is False


def test_start_reports_the_port_and_token_of_a_running_server(monkeypatch):
    monkeypatch.setattr(nativehost.instance, "find_running", lambda port: 8790)
    monkeypatch.setattr(nativehost, "_spawn_server", lambda: pytest.fail("already running"))
    reply = nativehost.handle({"cmd": "start"})
    assert reply["ok"] and reply["port"] == 8790 and len(reply["token"]) >= 20


def test_start_launches_ytmine_and_waits_for_it(monkeypatch):
    answers = iter([None, None, 8787])
    spawned = []
    monkeypatch.setattr(nativehost.instance, "find_running", lambda port: next(answers))
    monkeypatch.setattr(nativehost, "_spawn_server", lambda: spawned.append(True))
    monkeypatch.setattr(nativehost.time, "sleep", lambda s: None)
    assert nativehost.ensure_running() == 8787
    assert spawned == [True]


def test_start_gives_up_when_ytmine_never_answers(monkeypatch):
    monkeypatch.setattr(nativehost.instance, "find_running", lambda port: None)
    monkeypatch.setattr(nativehost, "_spawn_server", lambda: None)
    monkeypatch.setattr(nativehost, "START_TIMEOUT", 0)
    assert nativehost.ensure_running(timeout=0) is None


def test_main_speaks_only_the_protocol(monkeypatch):
    stdin, stdout = framed({"cmd": "nope"}), io.BytesIO()
    wrappers = io.TextIOWrapper(stdin), io.TextIOWrapper(stdout)  # kept alive: main swaps them
    monkeypatch.setattr(sys, "stdin", wrappers[0])
    monkeypatch.setattr(sys, "stdout", wrappers[1])
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    assert nativehost.main() == 0
    stdout.seek(0)
    assert nativehost.read_message(stdout) == {"ok": False, "error": "unknown-command"}
    assert stdout.read() == b""


# -- registering the host -------------------------------------------------------------------


class FakeRegistry(dict):
    def set(self, key, value):
        self[key] = value

    def delete(self, key):
        return self.pop(key, None) is not None


def test_windows_registers_with_the_registry(tmp_path, monkeypatch, config_dir):
    monkeypatch.setattr(sys, "executable", r"C:\My Python\python.exe")
    where = Places("Windows", tmp_path / "home", {})
    registry = FakeRegistry()
    done = nativehost.set_browser_start(True, where, registry)
    chrome_key = f"Software\\Google\\Chrome\\NativeMessagingHosts\\{nativehost.HOST_NAME}"
    firefox_key = f"Software\\Mozilla\\NativeMessagingHosts\\{nativehost.HOST_NAME}"
    assert "HKCU\\" + chrome_key in done and len(registry) == 5
    chrome = json.loads(Path(registry[chrome_key]).read_text(encoding="utf-8"))
    firefox = json.loads(Path(registry[firefox_key]).read_text(encoding="utf-8"))
    assert chrome["allowed_origins"] == [f"chrome-extension://{nativehost.CHROME_EXTENSION_ID}/"]
    assert firefox["allowed_extensions"] == [nativehost.FIREFOX_EXTENSION_ID]
    script = config_dir / "browser-host" / "ytmine-host.bat"
    assert chrome["path"] == str(script) and chrome["type"] == "stdio"
    assert script.read_bytes() == (
        b'@echo off\r\n"C:\\My Python\\python.exe" -m ytmine.nativehost %*\r\n'
    )
    assert nativehost.browser_start_enabled(where)

    nativehost.set_browser_start(False, where, registry)
    assert not registry and not script.exists()
    assert not nativehost.browser_start_enabled(where)


def test_linux_registers_only_with_browsers_that_exist(tmp_path, monkeypatch, config_dir):
    monkeypatch.setattr(sys, "executable", SPACEY_PYTHON)
    home = tmp_path / "home"
    (home / ".config" / "google-chrome").mkdir(parents=True)
    (home / ".mozilla").mkdir()
    where = Places("Linux", home, {})
    done = nativehost.set_browser_start(True, where)
    chrome = home / ".config" / "google-chrome" / "NativeMessagingHosts"
    firefox = home / ".mozilla" / "native-messaging-hosts"
    name = f"{nativehost.HOST_NAME}.json"
    assert sorted(done) == sorted([str(chrome / name), str(firefox / name)])
    assert not (home / ".config" / "chromium").exists()
    script = config_dir / "browser-host" / "ytmine-host"
    assert script.read_text(encoding="utf-8") == (
        "#!/bin/sh\nexec '/opt/my tools/bin/python3' -m ytmine.nativehost \"$@\"\n"
    )
    assert json.loads((chrome / name).read_text(encoding="utf-8"))["path"] == str(script)
    assert "allowed_extensions" in json.loads((firefox / name).read_text(encoding="utf-8"))

    nativehost.set_browser_start(False, where)
    assert not (chrome / name).exists() and not script.exists()


def test_mac_folders(tmp_path):
    home = tmp_path / "home"
    support = home / "Library" / "Application Support"
    (support / "Microsoft Edge").mkdir(parents=True)
    (support / "Mozilla").mkdir()
    nativehost.set_browser_start(True, Places("Darwin", home, {}))
    name = f"{nativehost.HOST_NAME}.json"
    assert (support / "Microsoft Edge" / "NativeMessagingHosts" / name).exists()
    assert (support / "Mozilla" / "NativeMessagingHosts" / name).exists()


def test_no_browser_is_an_error(tmp_path):
    with pytest.raises(launcher.LauncherError):
        nativehost.set_browser_start(True, Places("Linux", tmp_path / "home", {}))


def test_cli_browser_start(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    (home / ".config" / "chromium").mkdir(parents=True)
    here = Places("Linux", home, {})
    monkeypatch.setattr(launcher.Places, "here", classmethod(lambda cls: here))
    assert cli.main(["--browser-start", "on"]) == 0
    assert "can now start ytmine" in capsys.readouterr().out
    assert cli.main(["--browser-start", "off"]) == 0
    assert "no longer start" in capsys.readouterr().out
    assert cli.main(["--browser-start", "off"]) == 0
    assert "was off" in capsys.readouterr().out

    nowhere = Places("Linux", tmp_path / "empty", {})
    monkeypatch.setattr(launcher.Places, "here", classmethod(lambda cls: nowhere))
    assert cli.main(["--browser-start", "on"]) == 1
