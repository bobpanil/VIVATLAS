# VIVATLAS Clipper (browser extension)

A one-click "add tool" for [VIVATLAS](https://github.com/bobpanil/vivatlas). Grab the
page you're on (or paste a link), choose public or private, and send it to your
catalogue — then keep browsing while VIVATLAS processes it.

## What it does

- **Signed in automatically** — the clipper adopts your browser's own VIVATLAS session
  (it reads the `vivatlas_session` cookie), so if you're signed in on the site you're
  signed in here, with no separate extension login. Not signed in in the browser? It
  falls back to email + password → MFA, one step at a time.
- **Capture the current page** — reads the readable text (like a read-later clipper) in
  the background and sends the URL, title and text. **Rescan** re-reads the page if it
  changed. Pages that can't be read (`chrome://`, the Web Store, the PDF viewer) still
  save their link and title.
- **Or paste a link** — edit the Link field and send that instead.
- **Choose the zone per clip** — the visibility button beside the title cycles
  **default → private → public**. "Default" uses whatever you picked in the cog; the
  lock means private, the globe means public.
- **Processed into your library** — a GitHub repo is imported into a full card; any
  other page is summarised by the AI from the captured text. Both land as real cards in
  the zone you chose — not left sitting in drafts.

## The cog (settings)

The gear in the top-right holds the things you set once and forget:

- **Signed in as** — the account this clipper is using.
- **Server** — the VIVATLAS it talks to.
- **Default "Save as"** — Private or Public, used by any clip left on "default".
- **Sign out** — clears the token and the session.

## Install

**Easiest — from VIVATLAS.** Open your VIVATLAS, go to **Settings → Browser extension**,
and use the **Chrome/Edge** or **Firefox** download. That build is pre-wired to your
server (no "enter server" step) and signs in from your browser session (no extension
login) — the page walks you through the one-time load.

**Chrome / Edge / Brave (unpacked):**

1. Open `chrome://extensions`, turn on **Developer mode** (top right).
2. Click **Load unpacked** and pick the unzipped `vivatlas-clipper` folder.

**Firefox (temporary add-on):**

1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on** and choose `manifest.json` in the folder. Firefox
   drops temporary add-ons on restart — to keep it, sign the folder into an `.xpi` at
   [addons.mozilla.org](https://addons.mozilla.org) (free Mozilla account) and install that.

Manifest V3, cross-browser (Chromium `chrome.*` and Firefox `browser.*` via one shim).
A source checkout leaves the server blank; the popup then asks for it once.

## How it talks to VIVATLAS

All under `/api/ext` on your server:

| Endpoint            | Purpose                                              |
|---------------------|------------------------------------------------------|
| `POST /login`       | email + password → token, or `mfa_required` + ticket |
| `POST /mfa`         | ticket + code → token                                |
| `GET  /session`     | is the token still good (returns the account)        |
| `POST /logout`      | revoke the session                                   |
| `POST /add`         | `{url, title, text, shared}` → processed into a card |

The token is sent as `Authorization: Bearer …`; the same token is set as the
`vivatlas_session` cookie so the web UI opens authenticated.

## Notes

- The extension only asks for host permission for **your** server (requested when you
  set the address), plus `activeTab`/`scripting` to read the page you explicitly clip.
- One account per browser: signing in here (or in the web UI) shares the same
  `vivatlas_session` cookie, so the extension and the website are always the same user.
- Nothing is sent anywhere except the VIVATLAS server you configured.
