# VivAtlas

A catalogue of skills, agents and tools from your Git repositories. Multi-user: everyone has their own sign-in, their own folders and sources; the shared catalogue is common to all.

> **VivAtlas is a viewer, not an owner.** It reads *public* Git repositories and helps you find and view the tools inside them. It does **not** own — and claims no rights in — any of the repositories, code, names or trademarks it catalogues or links to. Those belong to their authors and are governed by their own licenses.

**License:** [Business Source License 1.1](LICENSE) — free to use, deploy and modify, including commercially; you may **not** sell or resell VivAtlas itself (as a product or a hosted service). Converts to Apache-2.0 on 2030-07-21.

Access from ChatGPT and Claude Code: [docs/MCP.md](docs/MCP.md). Deploy on TrueNAS: [docs/DEPLOY-TRUENAS.md](docs/DEPLOY-TRUENAS.md).

## What already works

- **Card catalogue.** One repository → one card: name, three levels of description (short / normal / technical), preview, tags (automatic, with source and confidence + manual, manual wins).
- **Search** by words (SQLite FTS) and by meaning (vectors), bilingual — a Russian query finds an English tool.
- **Recommendations** — three options for the task with an explanation, or an honest "nothing fits".
- **Folders** — shared (run by the admin) and personal (everyone has their own); a card can be dragged into a folder. Git is untouched in the process.
- **Zones** — a card is private or shared (in the catalogue); favourites, drafts, change feed and the "stale" feed.
- **People.** Sign-in by password, the first to sign in becomes the owner. Invitations by link/email, open registration (toggle), two-step sign-in (TOTP + backup codes), password reset by email.
- **Account.** Change email/password, deletion, profile photo (→ WebP) or an avatar from a ready-made set (classical busts), personal folders and sources.
- **Admin panel.** People, access and invitations, shared folders, email (SMTP), integrations (addresses/tokens/models on top of `.env`, applied without a restart).
- **Interface.** Custom rendering on the server, no build step. Languages: English (default), Russian, Hebrew (RTL). Themes: light / dark / OLED / system. Works from a phone too.
- **Sources.** Gitea (shared and personal) and GitHub (an account's or organisation's public repositories), scanned daily and on demand. Failed AI summaries are retried automatically.
- **Adding.** One door: a link, site, screenshot or reel → candidates with stars → plan → import. An address named by a model is always verified with the host.
- **Browser extension.** A Chrome/Chromium extension (`extension/`) clips the current page or a pasted link into your catalogue, public or private, from any tab. See [extension/README.md](extension/README.md).
- **Upstream.** A card remembers its source; `upstream` compares, `update` installs a new version only where you have not touched the file.
- **Outward.** REST API and MCP server for ChatGPT / Claude Code.

The main rule is unchanged: **the program writes nothing to Git and does not scan private repositories.** Reading public repositories only.

## Running

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -e ".[dev]"    # Windows
# .venv/bin/python -m pip install -e ".[dev]"          # Linux

cp .env.example .env      # SECRET_KEY is required; Gitea address and keys are optional (can be set from the admin panel)

.venv/Scripts/python.exe -m vivatlas.cli init-db                       # create/update the database
.venv/Scripts/python.exe -m vivatlas.cli scan                          # fetch repositories
.venv/Scripts/python.exe -m vivatlas.cli serve --host 0.0.0.0 --port 8710
```

Opens at `http://127.0.0.1:8710` (with `--host 0.0.0.0` — also from a phone on the same network). The first person to go through `/setup` becomes the owner.

> **After updating the code, run `init-db`** — it adds new columns to the database. `serve` does not run migrations: bring up new code on an old database and pages with missing fields will break.

## Tests

```bash
.venv/Scripts/python.exe -m pytest
.venv/Scripts/python.exe -m ruff check src tests
```

## Layout

```
src/vivatlas/
  config.py            settings from .env
  runtime_settings.py  overrides from the database on top of .env (edited from the admin panel)
  db.py                connection to SQLite
  models.py            tables
  migrate.py           init-db: add missing columns/indexes, rebuild search
  security.py          passwords, secret encryption, secret key
  twofactor.py         two-step sign-in (TOTP + backup codes)
  auth.py, auth_web.py sign-in, registration, invitations, password reset
  admin_web.py         admin panel (people, access, email, integrations)
  settings_web.py      personal settings, avatars, sources, folders
  web.py               catalogue, cards, adding
  api.py               app assembly, REST, /avatar, static
  filters.py           visibility: own + shared
  categories.py        folder permissions (shared/personal)
  i18n.py, translations*.py   translations (en/ru/he), RTL
  mailer.py            emails (password reset, invitations)
  avatars.py           uploaded photo → square WebP
  usericons.py         default avatar set (static/usericons)
  scanner.py, indexer.py  scanning + the private-repo rule, index
  mcp_server.py        access from ChatGPT / Claude Code
  cli.py               terminal commands (init-db, scan, serve, embed, upstream…)
  providers/
    base.py            common interface to the host (the "socket")
    gitea.py           Gitea
    github.py          GitHub (an account's public repositories)
  ext_api.py           JSON API for the browser extension (/api/ext)
  templates/, static/  pages and styles (custom app.css, no build step)

extension/             Chrome/Chromium extension (Manifest V3) — clip pages into VivAtlas
```

Adding another host: implement the `providers/base.py` interface in a new provider and wire it in `providers/__init__.py`. The rest of the code stays the same.
