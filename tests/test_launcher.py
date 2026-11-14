"""Shortcuts that start ytmine without a terminal."""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

from ytmine import cli, launcher
from ytmine.launcher import Places

SPACEY_PYTHON = "/opt/my tools/bin/python3"


def places(tmp_path, system, **env):
    return Places(system, tmp_path / "home", env)


@pytest.fixture
def spacey_python(monkeypatch):
    monkeypatch.setattr(sys, "executable", SPACEY_PYTHON)


def test_the_icons_ship_with_the_package():
    for name in ("icon.png", "icon.ico", "icon.icns"):
        assert (launcher.STATIC / name).stat().st_size > 500


def test_shortcuts_run_this_python_in_the_background():
    assert launcher.launch_args(False) == ["-m", "ytmine", "--background"]
    assert launcher.launch_args(True)[-1] == "--no-browser"


def test_windows_uses_the_windowless_python(tmp_path, monkeypatch):
    (tmp_path / "python.exe").write_text("")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    assert launcher.python_for_launch("Windows") == tmp_path / "python.exe"  # no pythonw yet
    (tmp_path / "pythonw.exe").write_text("")
    assert launcher.python_for_launch("Windows") == tmp_path / "pythonw.exe"
    assert launcher.python_for_launch("Linux") == tmp_path / "python.exe"


# -- Linux and other XDG desktops ---------------------------------------------------------


@pytest.mark.parametrize(
    ("arg", "quoted"),
    [
        ("-m", "-m"),
        ("/usr/bin/python3", "/usr/bin/python3"),
        ("/opt/my tools/python", '"/opt/my tools/python"'),
        ('a"b', '"a\\"b"'),
        ("$HOME", '"\\$HOME"'),
        ("50%", "50%%"),
    ],
)
def test_desktop_entry_quoting(arg, quoted):
    assert launcher._desktop_quote(arg) == quoted


def test_linux_menu_entry(tmp_path, spacey_python):
    where = places(tmp_path, "Linux")
    (entry,) = launcher.create_shortcut(where)
    assert entry == tmp_path / "home" / ".local" / "share" / "applications" / "ytmine.desktop"
    text = entry.read_text(encoding="utf-8")
    assert text.startswith("[Desktop Entry]\n")
    python = launcher._desktop_quote(str(Path(SPACEY_PYTHON)))
    assert python.startswith('"')  # the space forces quotes
    assert f"Exec={python} -m ytmine --background\n" in text
    assert "Terminal=false\n" in text
    icon = next(line for line in text.splitlines() if line.startswith("Icon="))[5:]
    assert Path(icon).is_file()
    assert launcher.shortcut_exists(where)
    assert launcher.remove_shortcut(where) == [entry]
    assert not launcher.shortcut_exists(where)
    assert launcher.remove_shortcut(where) == []


def test_linux_follows_xdg_data_home(tmp_path):
    where = places(tmp_path, "Linux", XDG_DATA_HOME=str(tmp_path / "data"))
    (entry,) = launcher.create_shortcut(where)
    assert entry == tmp_path / "data" / "applications" / "ytmine.desktop"


# -- macOS ---------------------------------------------------------------------------------


def test_mac_app_bundle(tmp_path, spacey_python):
    where = places(tmp_path, "Darwin")
    (app,) = launcher.create_shortcut(where)
    assert app == tmp_path / "home" / "Applications" / "ytmine.app"
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleExecutable"] == "ytmine"
    assert info["LSUIElement"] is True
    assert (app / "Contents" / "Resources" / f"{info['CFBundleIconFile']}.icns").is_file()
    script = (app / "Contents" / "MacOS" / "ytmine").read_bytes().decode()
    python = str(Path(SPACEY_PYTHON))
    assert script == f"#!/bin/sh\nexec '{python}' -m ytmine --background \"$@\"\n"
    if sys.platform != "win32":
        assert (app / "Contents" / "MacOS" / "ytmine").stat().st_mode & 0o111
    # Making it again replaces it cleanly; removing deletes the whole bundle.
    launcher.create_shortcut(where)
    assert launcher.remove_shortcut(where) == [app]
    assert not app.exists()


# -- Windows (real .lnk files, so only on Windows) ----------------------------------------


def _read_lnk(path: Path) -> dict:
    script = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:P);"
        "$s.TargetPath; $s.Arguments; $s.IconLocation"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        env={**__import__("os").environ, "P": str(path)},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return {"target": out[0], "args": out[1], "icon": out[2]}


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows to make .lnk files")
def test_windows_shortcuts(tmp_path):
    where = places(
        tmp_path,
        "Windows",
        APPDATA=str(tmp_path / "Roaming"),
        YTMINE_DESKTOP_DIR=str(tmp_path / "My Desktop"),
    )
    desktop, start = launcher.create_shortcut(where)
    assert desktop == tmp_path / "My Desktop" / "ytmine.lnk"
    assert start == where.start_menu() / "ytmine.lnk"
    assert where.start_menu().is_relative_to(tmp_path / "Roaming")
    lnk = _read_lnk(desktop)
    assert Path(lnk["target"]) == launcher.python_for_launch("Windows")
    assert lnk["args"] == "-m ytmine --background"
    assert lnk["icon"].lower().startswith(str(launcher.STATIC / "icon.ico").lower())
    assert launcher.shortcut_exists(where)
    assert sorted(launcher.remove_shortcut(where)) == sorted([desktop, start])
    assert not desktop.exists() and not start.exists()


# -- the command line ---------------------------------------------------------------------


@pytest.fixture
def linux_here(tmp_path, monkeypatch):
    where = places(tmp_path, "Linux")
    monkeypatch.setattr(launcher.Places, "here", classmethod(lambda cls: where))
    for name, value in {"DATA": "data", "CACHE": "cache", "CONFIG": "config"}.items():
        monkeypatch.setenv(f"YTMINE_{name}_DIR", str(tmp_path / value))
    return where


def test_cli_creates_and_removes_the_shortcut(linux_here, capsys):
    assert cli._shortcut_tip().startswith("Tip:")
    assert cli.main(["--create-shortcut"]) == 0
    out = capsys.readouterr().out
    assert "Added a ytmine shortcut" in out and "ytmine.desktop" in out
    assert cli._shortcut_tip() == ""  # no more tip once it exists
    assert cli.main(["--remove-shortcut"]) == 0
    assert "Removed" in capsys.readouterr().out
    assert cli.main(["--remove-shortcut"]) == 0
    assert "There was no shortcut" in capsys.readouterr().out


def test_cli_reports_a_failure(linux_here, monkeypatch, capsys):
    def broken(places=None):
        raise launcher.LauncherError("no PowerShell here")

    monkeypatch.setattr(launcher, "create_shortcut", broken)
    assert cli.main(["--create-shortcut"]) == 1
    assert "no PowerShell here" in capsys.readouterr().err
