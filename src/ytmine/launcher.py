"""Start ytmine without a terminal: a shortcut to click, and starting at login.

Each shortcut runs this same Python with `-m ytmine --background`, so it keeps working however
ytmine was installed (pipx, pip, a virtual environment). Nothing is written to the registry or
system folders; everything lives in the user's own folders and the matching --remove or
`--autostart off` deletes it again.

- Windows: .lnk files on the desktop, in the Start menu and (at login) in the Startup folder. They
  start pythonw.exe, which opens no console window.
- macOS: a small ytmine.app in ~/Applications, and a LaunchAgent for login.
- Linux and other XDG desktops: a .desktop entry in the applications menu, and one in autostart.
"""

from __future__ import annotations

import os
import platform
import plistlib
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

STATIC = Path(__file__).parent / "static"
NAME = "ytmine"
DESCRIPTION = "Mine Mandarin sentences from YouTube into Anki"
MAC_BUNDLE_ID = "io.github.soarcross.ytmine"
MAC_AGENT = MAC_BUNDLE_ID + ".login"


class LauncherError(Exception):
    """A shortcut could not be made or removed. The message is meant for the user."""


@dataclass
class Places:
    """Where shortcuts go on this computer. Tests build one pointing into a temporary folder."""

    system: str
    home: Path
    env: dict

    @classmethod
    def here(cls) -> Places:
        return cls(platform.system(), Path.home(), dict(os.environ))

    # -- Windows
    def _roaming(self) -> Path:
        return Path(self.env.get("APPDATA") or self.home / "AppData" / "Roaming")

    def start_menu(self) -> Path:
        return self._roaming() / "Microsoft" / "Windows" / "Start Menu" / "Programs"

    def startup(self) -> Path:
        return self.start_menu() / "Startup"

    def desktop(self) -> Path:
        override = self.env.get("YTMINE_DESKTOP_DIR")  # tests; also lets users choose
        if override:
            return Path(override)
        if self.system == "Windows":
            # The desktop can be moved (OneDrive does), so ask Windows where it is.
            found = _powershell("[Environment]::GetFolderPath('Desktop')", {}).strip()
            if found:
                return Path(found)
        return self.home / "Desktop"

    # -- macOS
    def mac_app(self) -> Path:
        return self.home / "Applications" / f"{NAME}.app"

    def mac_agent(self) -> Path:
        return self.home / "Library" / "LaunchAgents" / f"{MAC_AGENT}.plist"

    # -- Linux and other XDG desktops
    def xdg_menu_entry(self) -> Path:
        data = self.env.get("XDG_DATA_HOME") or self.home / ".local" / "share"
        return Path(data) / "applications" / f"{NAME}.desktop"

    def xdg_autostart(self) -> Path:
        config = self.env.get("XDG_CONFIG_HOME") or self.home / ".config"
        return Path(config) / "autostart" / f"{NAME}.desktop"


def python_for_launch(system: str) -> Path:
    """The interpreter running ytmine now; on Windows its windowless twin, pythonw.exe."""
    exe = Path(sys.executable)
    if system == "Windows":
        windowless = exe.with_name("pythonw.exe")
        if windowless.exists():
            return windowless
    return exe


def launch_args(at_login: bool) -> list[str]:
    # At login nobody asked for a browser tab; the extension or the shortcut opens the page.
    return ["-m", "ytmine", "--background", *(["--no-browser"] if at_login else [])]


# -- the shortcut -------------------------------------------------------------------------


def create_shortcut(places: Places | None = None) -> list[Path]:
    """Make the clickable shortcut(s). Returns what was written."""
    places = places or Places.here()
    python = python_for_launch(places.system)
    if places.system == "Windows":
        made = []
        for folder in (places.desktop(), places.start_menu()):
            made.append(_write_lnk(folder / f"{NAME}.lnk", python, launch_args(False), places))
        return made
    if places.system == "Darwin":
        return [_write_mac_app(places.mac_app(), python)]
    entry = places.xdg_menu_entry()
    _write_text(entry, _desktop_entry(python, launch_args(False)))
    return [entry]


def remove_shortcut(places: Places | None = None) -> list[Path]:
    places = places or Places.here()
    if places.system == "Windows":
        targets = [places.desktop() / f"{NAME}.lnk", places.start_menu() / f"{NAME}.lnk"]
    elif places.system == "Darwin":
        targets = [places.mac_app()]
    else:
        targets = [places.xdg_menu_entry()]
    return [path for path in targets if _remove(path)]


def shortcut_exists(places: Places | None = None) -> bool:
    """Cheap check (no PowerShell), used for the start-up tip."""
    places = places or Places.here()
    if places.system == "Windows":
        return (places.start_menu() / f"{NAME}.lnk").exists()
    if places.system == "Darwin":
        return places.mac_app().exists()
    return places.xdg_menu_entry().exists()


# -- starting at login --------------------------------------------------------------------


def set_autostart(on: bool, places: Places | None = None) -> list[Path]:
    """Start ytmine (in the background, no browser tab) when the user logs in, or stop doing so.
    Returns the files written or removed."""
    places = places or Places.here()
    python = python_for_launch(places.system)
    args = launch_args(True)
    if places.system == "Windows":
        path = places.startup() / f"{NAME}.lnk"
        return [_write_lnk(path, python, args, places)] if on else _removed(path)
    if places.system == "Darwin":
        path = places.mac_agent()
        if not on:
            return _removed(path)
        agent = {
            "Label": MAC_AGENT,
            "ProgramArguments": [str(python), *args],
            "RunAtLoad": True,
            "ProcessType": "Interactive",
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(plistlib.dumps(agent))
        return [path]
    path = places.xdg_autostart()
    if not on:
        return _removed(path)
    _write_text(path, _desktop_entry(python, args, extra="X-GNOME-Autostart-enabled=true\n"))
    return [path]


def autostart_enabled(places: Places | None = None) -> bool:
    places = places or Places.here()
    if places.system == "Windows":
        return (places.startup() / f"{NAME}.lnk").exists()
    if places.system == "Darwin":
        return places.mac_agent().exists()
    return places.xdg_autostart().exists()


# -- writers ------------------------------------------------------------------------------


def _powershell(script: str, env: dict) -> str:
    """Run a PowerShell snippet. Values travel in environment variables, never in the script
    text, so paths with quotes or spaces cannot break it."""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LauncherError(f"Could not run PowerShell to make the shortcut ({exc}).") from exc
    if done.returncode != 0:
        raise LauncherError(f"PowerShell could not make the shortcut: {done.stderr.strip()}")
    return done.stdout


_LNK_SCRIPT = """
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:YTM_LNK)
$s.TargetPath = $env:YTM_TARGET
$s.Arguments = $env:YTM_ARGS
$s.WorkingDirectory = $env:YTM_DIR
$s.IconLocation = $env:YTM_ICON
$s.Description = $env:YTM_DESC
$s.Save()
"""


def _write_lnk(path: Path, python: Path, args: list[str], places: Places) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    env = {
        "YTM_LNK": str(path),
        "YTM_TARGET": str(python),
        "YTM_ARGS": subprocess.list2cmdline(args),
        "YTM_DIR": str(places.home),
        "YTM_ICON": f"{STATIC / 'icon.ico'},0",
        "YTM_DESC": DESCRIPTION,
    }
    _powershell(_LNK_SCRIPT, env)
    if not path.exists():
        raise LauncherError(f"The shortcut was not created at {path}.")
    return path


def _write_mac_app(app: Path, python: Path) -> Path:
    """A minimal app bundle whose executable is a shell script that starts ytmine."""
    if app.exists():
        shutil.rmtree(app)
    macos = app / "Contents" / "MacOS"
    resources = app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir(parents=True)
    info = {
        "CFBundleName": NAME,
        "CFBundleDisplayName": NAME,
        "CFBundleIdentifier": MAC_BUNDLE_ID,
        "CFBundleExecutable": NAME,
        "CFBundleIconFile": "icon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "LSUIElement": True,  # no Dock icon: the server has no window of its own
    }
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    shutil.copyfile(STATIC / "icon.icns", resources / "icon.icns")
    script = macos / NAME
    args = " ".join(shlex.quote(a) for a in launch_args(False))
    line = f'exec {shlex.quote(str(python))} {args} "$@"'
    script.write_bytes(f"#!/bin/sh\n{line}\n".encode())  # LF only, whatever OS writes it
    script.chmod(0o755)
    return app


def _desktop_quote(arg: str) -> str:
    """Quote one Exec= argument as the freedesktop Desktop Entry spec asks."""
    arg = arg.replace("%", "%%")
    if not any(c in arg for c in " \t\n\"'\\><~|&;$*?#()`"):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def _desktop_entry(python: Path, args: list[str], extra: str = "") -> str:
    command = " ".join(_desktop_quote(a) for a in [str(python), *args])
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={NAME}\n"
        f"Comment={DESCRIPTION}\n"
        f"Exec={command}\n"
        f"Icon={STATIC / 'icon.png'}\n"
        "Terminal=false\n"
        "Categories=Education;Languages;\n"
        f"{extra}"
    )


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _remove(path: Path) -> bool:
    if path.is_dir():
        shutil.rmtree(path)
        return True
    if path.exists():
        path.unlink()
        return True
    return False


def _removed(path: Path) -> list[Path]:
    return [path] if _remove(path) else []
