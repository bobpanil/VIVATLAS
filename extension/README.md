# VivAtlas Clipper (Chrome extension)

A one-click "add tool" for [VivAtlas](https://github.com/bobpanil/vivatlas). Grab the
page you're on (or paste a link), choose public or private, and send it to your
catalogue — then keep browsing while VivAtlas processes it.

## What it does

- **Sign in once** — server address → email + password → MFA code. The extension keeps
  a session token and also signs the web UI in, so **Open VivAtlas** lands you inside
  with no second login.
- **Capture the current page** — reads the readable text (like a read-later clipper),
  shows a preview so you can confirm it grabbed the right thing, and sends the URL,
  title and text.
- **Or paste a link** — edit the Link field and send that instead.
- **Public or private** — a GitHub repo is imported into a full card; anything else is
  kept as a draft you can edit later. Either lands where you chose.
- **Sign out** clears the token and the session.

## Install (unpacked)

1. Open `chrome://extensions`.
2. Turn on **Developer mode** (top right).
3. Click **Load unpacked** and pick this `extension/` folder.
4. Pin the VivAtlas icon, click it, and enter your server address (e.g.
   `https://vivatlas.example.com`). Chrome will ask permission for that server —
   approve it. Then sign in.

Works in any Chromium browser (Chrome, Edge, Brave) with Manifest V3.

## How it talks to VivAtlas

All under `/api/ext` on your server:

| Endpoint            | Purpose                                              |
|---------------------|------------------------------------------------------|
| `POST /login`       | email + password → token, or `mfa_required` + ticket |
| `POST /mfa`         | ticket + code → token                                |
| `GET  /session`     | is the token still good                              |
| `POST /logout`      | revoke the session                                   |
| `POST /add`         | `{url, title, text, shared}` → import or draft       |

The token is sent as `Authorization: Bearer …`; the same token is set as the
`vivatlas_session` cookie so the web UI opens authenticated.

## Notes

- The extension only asks for host permission for **your** server (requested when you
  set the address), plus `activeTab`/`scripting` to read the page you explicitly clip.
- Some pages can't be read (`chrome://`, the Web Store, the PDF viewer) — the link and
  title are still saved.
- Nothing is sent anywhere except the VivAtlas server you configured.
