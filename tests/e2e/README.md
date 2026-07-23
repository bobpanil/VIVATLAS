# End-to-end accessibility & compatibility suite

Automated WCAG 2.2 checks with [Playwright](https://playwright.dev/python/) +
[axe-core](https://github.com/dequelabs/axe-core) (vendored as `axe.min.js`,
v4.10.2). Loads the real pages in Chromium across a viewport matrix and fails on
any **serious/critical** axe violation.

## One-time setup

```
.venv/Scripts/python -m pip install playwright
.venv/Scripts/python -m playwright install chromium
```

## Run

These tests hit a **running** server (they don't start one) and skip if none is
reachable, so `pytest tests/` stays green with no server up.

```
# 1. start a server against your dev DB
.venv/Scripts/python -m uvicorn vivatlas.api:app --host 127.0.0.1 --port 8710

# 2. run the suite (point it elsewhere with VIVATLAS_E2E_URL)
.venv/Scripts/python -m pytest tests/e2e -q
```

A session is minted directly against the app's database as the first user, so no
password is needed.

## Matrix & scope

- Pages: `/` (catalogue), `/settings`, `/add`.
- Viewports: 375×812 (phone), 768×1024 (tablet), 1280×900 (desktop). On phone the
  drawer is opened so axe checks it on its real background.
- **Gated:** serious/critical axe violations (real WCAG 2.2 A/AA failures).
- **Allow-listed** (advisory best-practice, not numbered SCs — tracked separately):
  `region`, `heading-order`, `landmark-one-main`, `landmark-unique`.

## Follow-ups

- Hermetic mode: spin up a server against a seeded temp DB inside a fixture so the
  suite can run in CI without a pre-started server.
- Add the en/ru/he (incl. RTL) and light/dark/oled dimensions to the matrix.
