# MCP access (for AI assistants)

VIVATLAS answers not only you through the browser, but also any AI assistant that
speaks the [Model Context Protocol](https://modelcontextprotocol.io). Ask "what do I
have for building a landing page?" and it digs into your catalogue and answers from
facts instead of making things up — and, signed in as you, it can also **add and edit**
cards.

Two ways to connect:

- **Local (stdio)** — a desktop client (e.g. Claude Desktop) launches VIVATLAS as a
  local process. Read-only, uses your local database directly.
- **Remote (OAuth over HTTP)** — a hosted client such as **ChatGPT** connects over the
  network and signs in *as a specific user*, so it sees that user's private + shared
  cards and can write on their behalf.

## What it can do

Read (both connection types):

| Tool | What it does |
|---|---|
| `catalog_overview` | What's there overall: how much of what, from which repositories |
| `search_artifacts` | Find by query, by meaning (understands Russian too) |
| `recommend_artifact` | Pick one for a task: what to take, why, what it can't do |
| `get_artifact` | Full card: three descriptions, tags, where it came from |
| `list_artifacts` | A list, optionally of a single type |
| `list_tags` | All tags with the number of tools |
| `list_recent_changes` | What appeared / changed / disappeared recently |
| `find_stale_artifacts` | What hasn't been touched in a long time |

Write (only when signed in over OAuth — never anonymously):

| Tool | What it does |
|---|---|
| `add_to_library` | Add a tool from a link (GitHub repo or any page); processed in the background |
| `edit_card` | Edit one of your cards (name, type, the three descriptions) |
| `list_folders` | Your folders (id + name), for filing |
| `file_card` | Put a card into (or take it out of) one of your folders |

Anonymously (no sign-in) the remote server exposes **only the read tools, over shared
cards** — someone else's private cards never leak through it.

## Local (stdio)

Point your MCP client's config at the CLI:

```json
{
  "mcpServers": {
    "vivatlas": {
      "command": "/path/to/vivatlas/.venv/bin/python",
      "args": ["-m", "vivatlas.cli", "mcp"],
      "cwd": "/path/to/vivatlas"
    }
  }
}
```

## Remote — connect ChatGPT

### One-time server setup

Set **`PUBLIC_URL`** in your server config (`.env`) to your public HTTPS address, no
trailing slash — e.g. `PUBLIC_URL=https://vivatlas.example.com`. This becomes the OAuth
issuer, which must be a stable absolute HTTPS URL. Without it the remote MCP stays
anonymous (read-only, shared cards) exactly as before.

Your connector address is then:

```
https://vivatlas.example.com/mcp-server/mcp
```

(Also shown, with a Copy button, in **Settings → ChatGPT**.)

### In ChatGPT

1. **Settings → Connectors → Advanced → turn on Developer mode.**
   (Requires a plan where custom/Developer-mode connectors are available.)
2. **Add a custom connector** and paste the address above.
3. ChatGPT sends you to **VIVATLAS to sign in and approve** — click **Allow**. It asks
   for read + write access as you.
4. Ask it, e.g. *"Search my VIVATLAS for a design kit"* to confirm it's connected.

Writes (adding/editing cards) trigger ChatGPT's own confirmation prompt. Anything it
adds lands in your **private** zone by default.

### Scan on a schedule

Create a **ChatGPT Scheduled Task**, for example:

> Each morning, scan my VIVATLAS library and suggest tools to add and card summaries to
> fix. Add clearly-missing tools to my private zone; list proposed edits for me to
> approve.

ChatGPT runs it on the schedule, calls the read tools to survey the catalogue, and
surfaces suggestions (and applies the ones you allow).

### Managing access

**Settings → ChatGPT** lists every app currently authorized against your account, each
with a **Revoke** button that invalidates its tokens immediately.

## Notes

- Responses are deliberately short and field-based: on the other end a model with
  limited memory reads them, and lists are capped at 20.
- `get_artifact` returns a `notes` field flagging weak data (low-confidence type, missing
  description) so the model knows what to trust.
- Tokens are stored only as hashes (like sign-in sessions). The OAuth issuer is your
  `PUBLIC_URL`; the flow is standard OAuth 2.1 with PKCE and dynamic client registration.
