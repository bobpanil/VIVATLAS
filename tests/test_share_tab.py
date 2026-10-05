"""Settings in the Android app: a "Share behaviour" tab, and no browser-extension tab.

The share default belongs to the phone and is kept by the app, so the Settings page
reads and writes it through the app's bridge (VivatlasNative). A browser has no such
bridge, so the tab must start hidden and only a script that finds the bridge may
reveal it. The extension tab is useless on a phone: hidden in the app, and by CSS on
any touch-only device.
"""

import re
from pathlib import Path

from vivatlas import translations

TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "vivatlas" / "templates"
CSS = Path(__file__).resolve().parents[1] / "src" / "vivatlas" / "static" / "app.css"


def test_share_tab_starts_hidden_and_only_the_bridge_reveals_it():
    html = (TEMPLATES / "settings.html").read_text(encoding="utf-8")
    tab = re.search(r'<button [^>]*data-tab="share"[^>]*>', html)
    assert tab and " hidden" in tab.group(0)
    panel = re.search(r'<section [^>]*data-panel="share"[^>]*>', html)
    assert panel and " hidden" in panel.group(0)
    i = html.index("typeof bridge.getShareShared !== 'function'")
    script = html[i - 400:i + 1600]
    assert "tab.hidden = false" in script
    assert "bridge.setShareShared(" in script
    assert "ext.hidden = true" in script


def test_no_separate_app_settings_entry_is_left():
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "app-settings-item" not in base and "openAppSettings" not in base


def test_extension_tab_hidden_on_touch_devices():
    css = CSS.read_text(encoding="utf-8")
    m = re.search(r"@media \(hover: none\) and \(pointer: coarse\) \{(.*?)\n\}", css, re.S)
    assert m and '.settab[data-tab="extension"]' in m.group(1) and "display: none" in m.group(1)


def test_share_strings_exist_in_every_language():
    src = Path(translations.__file__).read_text(encoding="utf-8")
    for key in ("settings.tab_share", "settings.share_title", "settings.share_tip",
                "settings.share_saved", "settings.share_app_version"):
        m = re.search(r'"' + re.escape(key) + r'": \{(.*?)\},\n', src)
        assert m, key
        langs = dict(re.findall(r'"(en|ru|he)": "([^"]+)"', m.group(1)))
        assert langs.get("en") and langs.get("ru") and langs.get("he"), key
    assert "{v}" in re.search(r'"settings\.share_app_version": \{(.*?)\},\n', src).group(1)
