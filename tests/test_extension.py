"""The browser extension's shape. These checks guard the security-relevant parts of the manifest
and keep the extension's copy of overlay.js in step with the web app's."""

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
EXT = ROOT / "extension"
STATIC = ROOT / "src" / "ytmine" / "static"


@pytest.fixture(scope="module")
def manifest():
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_is_mv3_with_the_expected_pieces(manifest):
    assert manifest["manifest_version"] == 3
    assert manifest["background"]["service_worker"] == "background.js"
    assert manifest["action"]["default_popup"] == "popup.html"
    assert manifest["options_ui"]["page"] == "options.html"


def test_permissions_are_minimal(manifest):
    # nativeMessaging only reaches the host that `ytmine --browser-start on` registers.
    assert manifest["permissions"] == ["storage", "nativeMessaging"]
    # No tabs, downloads, cookies, history or webRequest.
    assert not manifest.get("optional_permissions")
    assert "web_accessible_resources" not in manifest  # nothing the page can load from us
    assert "externally_connectable" not in manifest  # no other site can message the extension


def test_the_extension_may_only_reach_this_computer(manifest):
    hosts = manifest["host_permissions"]
    assert hosts and all(h.startswith(("http://127.0.0.1/", "http://localhost/")) for h in hosts)


def test_the_content_scripts_run_on_youtube_and_the_local_pairing_page_only(manifest):
    youtube, pairing = manifest["content_scripts"]
    assert youtube["matches"] == ["https://www.youtube.com/*"]
    assert youtube["js"] == ["shared.js", "overlay.js", "content.js"]  # order matters
    # The pairing helper runs on local pages only and needs nothing else.
    assert pairing["matches"] == ["http://127.0.0.1/*", "http://localhost/*"]
    assert pairing["js"] == ["pair.js"]
    assert "css" not in pairing


def test_the_pairing_helper_stays_quiet_on_other_local_pages():
    # Other local web servers must never see a request from it: it checks the marker first.
    source = (EXT / "pair.js").read_text(encoding="utf-8")
    marker = 'meta[name="ytmine-pairing"]'
    assert marker in source
    assert source.index(marker) < source.index("/api/pairing")
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<meta name="ytmine-pairing"' in page


def test_every_file_the_manifest_names_exists(manifest):
    scripts = manifest["content_scripts"]
    names = [
        *(name for script in scripts for name in script["js"]),
        *(name for script in scripts for name in script.get("css", [])),
        manifest["background"]["service_worker"],
        *manifest["background"]["scripts"],
        manifest["action"]["default_popup"],
        manifest["options_ui"]["page"],
    ]
    for name in names:
        assert (EXT / name).is_file(), f"missing {name}"
    for page in ("popup.html", "options.html"):
        html = (EXT / page).read_text(encoding="utf-8")
        for src in __import__("re").findall(r'<script src="([^"]+)"', html):
            assert (EXT / src).is_file(), f"{page} loads missing {src}"


def test_overlay_js_is_the_same_file_as_the_web_apps():
    """The extension cannot read files outside its folder, so it carries a copy. If this fails,
    copy src/ytmine/static/overlay.js over extension/overlay.js."""

    def text(path):
        return path.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert text(EXT / "overlay.js") == text(STATIC / "overlay.js")


def test_no_remote_code_and_no_inline_scripts():
    for path in EXT.glob("*"):
        if path.suffix not in {".js", ".html"}:
            continue
        source = path.read_text(encoding="utf-8")
        assert "eval(" not in source and "new Function(" not in source, path.name
        assert "http://" not in source.replace("http://127.0.0.1", "").replace(
            "http://localhost", ""
        )
        if path.suffix == ".html":
            assert "<script>" not in source, f"{path.name} has an inline script"


def test_only_the_background_worker_calls_fetch():
    """The content script's requests would carry youtube.com as their origin."""
    for name in ("content.js", "overlay.js", "popup.js", "options.js", "shared.js"):
        assert "fetch(" not in (EXT / name).read_text(encoding="utf-8"), name
    assert "fetch(" in (EXT / "background.js").read_text(encoding="utf-8")


def test_the_token_is_only_sent_to_this_computer():
    source = (EXT / "background.js").read_text(encoding="utf-8")
    assert '"127.0.0.1"' in source and '"localhost"' in source
    assert source.index("serverBase(server)") < source.index("TOKEN_HEADER]: token")


def test_the_banner_class_does_not_collide_with_the_overlays_popup_label():
    """The popup's "new / mined" label is .ytm-status inside overlay.js. The content script's
    page banner once used the same class, and content.css restyled the label."""
    assert ".ytm-status" in (EXT / "overlay.js").read_text(encoding="utf-8")
    for name in ("content.js", "content.css"):
        assert "ytm-status" not in (EXT / name).read_text(encoding="utf-8"), name
