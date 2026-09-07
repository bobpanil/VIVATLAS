# Getting the word out

Ready-to-paste text for the places where the people who'd want VIVATLAS already
are. Each block is written to be posted as-is; change the voice if it isn't yours.

## One-liners for curated lists (send as a pull request)

**awesome-mcp-servers** (under a "Knowledge & Memory" or "Developer Tools" heading):

```
- [bobpanil/VIVATLAS](https://github.com/bobpanil/VIVATLAS) 🐍 🏠 — A self-hosted catalogue of your skills, agents and MCP servers, searchable by meaning; exposes them to any MCP client with search, recommend and get-card tools.
```

**awesome-claude-code** / **awesome-claude-skills**:

```
- [VIVATLAS](https://github.com/bobpanil/VIVATLAS) — Self-hosted catalogue for the skills, agents and MCP servers scattered across your repositories. Every one becomes a card with a picture and a plain-language description; search by meaning; ask it from Claude over MCP.
```

**awesome-gitea**:

```
- [VIVATLAS](https://github.com/bobpanil/VIVATLAS) — Turns the tools in your Gitea (and GitHub) repositories into a searchable, multi-user catalogue with AI-written descriptions. Reads public repositories only; never writes to Git.
```

> Note: lists that require an OSI-approved licence (awesome-selfhosted among them)
> won't take a BSL-1.1 project. Don't submit there; it costs goodwill.

## Registries

- **Official MCP registry** — `server.json` at the repo root is the submission.
  Install the publisher (`brew install mcp-publisher` or the release binary),
  then `mcp-publisher login github` and `mcp-publisher publish`. The name is
  `io.github.bobpanil/vivatlas`, which the GitHub login authorises.
- **Glama** — `glama.json` at the repo root claims the listing; Glama indexes
  GitHub on its own and the file names you as maintainer.
- **PulseMCP** — a submission form at pulsemcp.com/submit; paste the README's
  first two paragraphs.
- **Smithery** hosts servers itself; a per-user self-hosted instance isn't a fit.

## Android

- **Obtainium** — nothing to submit; the README tells people to add the repo.
- **IzzyOnDroid** — request a listing (apt.izzysoft.de/fdroid, or their issue tracker)
  with the repo URL; they take the signed APK from the GitHub Release and the texts
  from `fastlane/`. Days.
- **F-Droid** — `android/fdroid/com.vivatlas.app.yml` is the ready, linted submission;
  `android/fdroid/README.md` has the four steps. Weeks. Expect the *NonFreeNet* badge.

## Show HN

**Title:** `Show HN: VIVATLAS – a self-hosted catalogue for your AI skills, agents and MCP servers`

**First comment (post it yourself, right after submitting):**

> I kept losing track of things: Claude skills I'd written, MCP servers I'd starred, a scraper somebody linked on Reddit, design kits in my own Gitea. Three months later I'd remember "there was a thing for that" and not find it.
>
> VIVATLAS is the shelf. Point it at your repositories and the links you clip; each becomes a card with a picture, a description written for a person, and tags. Search is by meaning as well as by word, and there's a recommender that gives three candidates for a task and says why — or admits nothing fits. It's also an MCP server, so Claude or ChatGPT can search your catalogue directly.
>
> Runs on your own machine (I run it on a NAS), multi-user, reads public repos only and never writes to Git. Python/FastAPI, SQLite, no build step. Licence is BSL 1.1 — free to use and modify, I just don't want it resold as a hosted service; it becomes Apache-2.0 in 2030.
>
> Happy to answer anything.

## Reddit

**r/selfhosted** — title: `I built a self-hosted catalogue for all the AI tools and scripts I kept losing track of`

**r/ClaudeAI** / **r/mcp** — title: `VIVATLAS: a catalogue of your skills and MCP servers that Claude can search over MCP`

Body for either: the Show HN comment above, minus the last line, plus the catalogue
screenshot (`docs/images/catalogue.webp`) as the post image. Reply to every comment
on the first day; that is where most of the value is.

## Before posting anywhere

- Repo description set; topics: `mcp`, `mcp-server`, `claude`, `ai-agents`,
  `self-hosted`, `catalog`, `gitea`, `fastapi`, `python`.
- `docs/social-preview.png` uploaded as the repository's social preview.
- A tagged release, so the repo doesn't read as a work in progress.
- The GIF at the top of the README plays (`docs/images/vivatlas.gif`).
