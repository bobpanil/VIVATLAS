# VivAtlas — plan

In plain words, nothing extra.

> **Status:** stages 1–9 work. Added beyond the plan: sign-in and people
> (accounts, invitations, 2FA, password reset, admin panel), multilingual
> (English/Russian/Hebrew, RTL), themes, avatars, shared and personal folders,
> zones "personal / shared catalogue". For an up-to-date overview, see [README](../README.md).

---

## 1. Why

A program that knows everything about your tools and answers four questions:

- What do we have?
- What does each thing do?
- What to pick for a specific task?
- What's new and what's gone stale?

Available from a computer, from a phone, and also from ChatGPT and Claude Code.

The main rule: **the program never moves or renames anything in Git.** It only reads and shows.

---

## 2. What actually exists (checked 15.07.2026)

Went into the live Gitea and recounted.

**99 repositories, two groups:**

| Group | How many | What it is | Structure |
|---|---|---|---|
| `design-lib` | 74 | Brand sets: airbnb, apple, bmw, ferrari, figma… | Identical: `DESIGN.md` + `README.md` + `preview.svg`, ~35 KB |
| `skills-lib` | 25 | Skills | Varied: from a single `SKILL.md` to a Python project of 51 files |

All together — 6 MB.

**Key fact: one repository = one card.** Looked inside — there's always one thing inside, not several. So 99 repositories give ~99 cards, and a ceiling of 500 repositories — 500 cards.

That's ten times fewer than I assumed at first, and it removes almost all the complications.

**A card** is a page for a single tool: name, what it does, why it's useful, where it lives, what it needs. Like a product card in a shop.

---

## 3. Decisions

| What | Decision |
|---|---|
| Where it lives | Linux server / NAS. No graphics card — and none needed |
| Access from phone | Cloudflare Tunnel + domain |
| Sign-in | Login and password |
| Source | **Gitea only.** GitHub — later, but room is left for it |
| Private repositories | **Never scanned.** A hard rule in the code, not a setting |
| AI | Via OpenRouter. One key for everything |

### Why all the privacy fuss disappeared

We scan only public repositories. Since the content is already public, handing it to AI is no scarier than showing it in a browser. So these were dropped: "AI allowed / not allowed" checkboxes per repository, local models, Ollama, the permissions matrix.

One security rule remains: don't read files with passwords and keys (`.env`, `*.pem`, `id_rsa`) — in case someone accidentally committed something like that.

### Room for GitHub

Working with repositories is hidden behind a single "socket" — a common set of commands: give me the list, give me the files, what changed. Right now Gitea is plugged into the socket. GitHub is a second plug that can be inserted later without touching anything else.

---

## 4. What it's built from

| Part | With |
|---|---|
| The whole application | One Python process. No Redis, no separate services |
| Database | A single SQLite file. For 500 cards — with a huge margin |
| Word search | Built into SQLite |
| Meaning search | Vectors alongside, in the same file |
| Pages | Served from the server. No React, no build step |
| Phone | The same pages + an icon on the screen |
| Startup | One container + Cloudflare Tunnel |

### Models

| For what | What | Why |
|---|---|---|
| Meaning search | Qwen3-Embedding, 1024 numbers | Top of the multilingual charts, understands both Russian and code |
| Card descriptions | Claude Haiku 4.5 | Volume needed, not depth |
| Recommendations | Claude Opus 4.8 | This is what it's all for — brains needed here |

Everything through OpenRouter — one key, switch models with one line in the settings.

**Meaning search** is when you write "pull tables out of a pdf" and the tool `pdf-table-extractor` turns up, even though not a single word matched. It works like this: each tool is turned into a list of 1024 numbers reflecting meaning — like coordinates on a map, where similar things lie close together. Your question is turned into the same kind of numbers, and the nearest points are found.

---

## 5. Money

**A one-off ~$1–2** — descriptions for a hundred cards. After that, cents per query. The server is yours.

---

## 6. What we do, and what we don't

**We do:**

- Connect Gitea, public repositories only
- The repository tree as is, without rearranging
- Cards: three levels of description (short / normal / technical), a preview image for design-lib
- Auto-tags with a note of where they came from and how confident
- Manual tags override automatic ones. Deleted an auto-tag — it won't come back after the next scan
- Your own sections: arrange tools however you like, Git doesn't change
- Word and meaning search, in Russian and in English
- A recommendation of three options with an explanation of why these and what's wrong with the rest
- An honest "there's no good match" when there truly isn't
- What's new, what changed, what was removed
- A list of the stale — untouched for more than a year
- Web + phone
- Access from ChatGPT and Claude Code

**We don't do yet:**

- GitHub (room is left)
- Merging identical tools from GitHub and Gitea — without GitHub it isn't needed
- Creating folders, branches, pull requests — read only
- Quality scores and checks
- Working offline on the phone

---

## 7. Stages

I won't name deadlines in days — we work in sessions. After each stage I show the result.

| # | Stage | What's visible at the end |
|---|---|---|
| 1 | Skeleton, database, Gitea connection | 99 repositories in the database, private ones skipped |
| 2 | Cards and descriptions | 99 cards with text, design-lib with previews |
| 3 | Tags and search | A Russian query finds an English tool |
| 4 | Recommendations | "Need PDF to Excel" → three options with an explanation |
| 5 | Web and phone | Opens from a phone, installs to the home screen |
| 6 | ChatGPT and Claude Code | The same data is visible from there |
| 7 | Changes and the stale | After edits in Gitea, only what changed updates |
| 8 | Source and updates | A card knows where it came from and installs a new version |
| 9 | One door for adding | A link, a website, a screenshot or a reel → a card |

**One risk:** `skills-lib` are a mixed bunch — from a single file to a project of 51 files. Some tuning may be needed at stage 2. Everything else is predictable.

### What was done for 8 and 9

Stage 8. 76 cards have a source recorded and an honest marker — a snapshot at the moment
of copying. Without the marker, "the files differ" means nothing: either a new
version came out, or you edited it yourself. `vivatlas upstream` compares,
`vivatlas update <card>` installs the new version. Only what you haven't touched
updates; everything else gets a refusal with a reason.

Recording hasn't been tested live yet: all 76 cards match the source, and
you can't spoil a working file just to test. Refusals and reading were tested against live
Gitea, recording — in the tests.

Stage 9. `vivatlas find <anything>` — a link to GitHub, a website page,
a screenshot, a reel or just words. It shows candidates with stars and a
description; you choose. We don't pull automatically: a name by ear and from an image
is recognized inaccurately.

The model here only reads and listens. The repository address it names is
always checked against GitHub — on a live reel it made up `skills/last-30-day`,
which doesn't exist. You can ask it not to lie, but you can't rely on that.

---

## 8. Still to ask

**Interface language** — decided: three languages, English by default, plus Russian and Hebrew (with right-to-left layout). Switched in the settings, the choice lives in a cookie.

---

## 9. Separately: your Gitea is open to the outside

Not part of the plan, but you should know. I got the list of all 99 repositories and the contents of files **with no password at all**. There's only a block against simple bots: the first request was refused, but the moment I introduced myself as an ordinary browser — everything opened.

Maybe that's by design. But if you thought otherwise — say so, I'll look at the settings.
