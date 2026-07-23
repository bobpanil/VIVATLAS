"""WCAG 2.2 gate: axe-core reports no serious/critical violations across the
viewport matrix, on the main authenticated pages.

Advisory best-practice findings that are NOT numbered WCAG 2.2 success criteria
(landmark/heading structure, and the CSS-only drawer checkbox sitting outside a
landmark) are allow-listed so the gate stays focused on real A/AA failures.
"""

import pathlib

import pytest

_AXE = (pathlib.Path(__file__).parent / "axe.min.js").read_text(encoding="utf-8")

# Best-practice rules (advisory, not numbered WCAG 2.2 SCs) — tracked, not gated.
ALLOWED = {"region", "heading-order", "landmark-one-main", "landmark-unique", "landmark-complementary-is-top-level"}

PAGES = ["/", "/settings", "/add"]
VIEWPORTS = [(375, 812), (768, 1024), (1280, 900)]


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("width,height", VIEWPORTS)
def test_no_serious_axe_violations(context, base_url, path, width, height):
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base_url + path, wait_until="networkidle")

    # On a phone the sidebar drawer is translated off-screen; open it so axe checks
    # its contents on their real (dark) background instead of mis-sampling the cream
    # canvas behind it (which yields false-positive contrast failures).
    if width < 768:
        page.evaluate("() => { const n = document.getElementById('navt'); if (n) n.checked = true; }")

    page.add_script_tag(content=_AXE)
    result = page.evaluate("async () => await axe.run(document, { resultTypes: ['violations'] })")
    page.close()

    serious = [
        v
        for v in result["violations"]
        if v["impact"] in ("serious", "critical") and v["id"] not in ALLOWED
    ]
    detail = "; ".join(f"{v['id']}({v['impact']}, {len(v['nodes'])} nodes)" for v in serious)
    assert not serious, f"{path} @ {width}x{height}: {detail}"
