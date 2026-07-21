# Access from ChatGPT and Claude Code

VivAtlas can answer not only you through the browser, but also your AI assistants.
Ask Claude Code "what do I have for building a landing page?" — it digs into
your catalogue and answers based on facts, instead of making things up.

Read-only, all of it. Nothing is written to Git or to the database.

## What it can do

| Tool | What it does |
|---|---|
| `catalog_overview` | What's there overall: how much of what, from which repositories |
| `search_artifacts` | Find by query. Understands Russian, searches by meaning |
| `recommend_artifact` | Pick one for the task: what to take, why, what it can't do |
| `get_artifact` | Full card: three descriptions, tags, where it came from |
| `list_artifacts` | A list, optionally of a single type |
| `list_tags` | All tags with the number of tools |

## Claude Code

Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "vivatlas": {
      "command": "D:\Software\Dev\VivAtlas\.venv\Scripts\python.exe",
      "args": ["-m", "vivatlas.cli", "mcp"],
      "cwd": "D:\Software\Dev\VivAtlas"
    }
  }
}
```

To check: ask Claude Code "show me an overview of my tool catalogue".

## ChatGPT

You need an internet-reachable address — that same Cloudflare Tunnel.

```
vivatlas serve --host 0.0.0.0
```

Server address: `https://your-domain/mcp-server/mcp`

## Why the answers are short

On the other end they're read by a model with limited memory. Excess text crowds
out the useful, so fields are returned rather than prose, and the documentation isn't
poured out in full. The list is capped at 20 cards, even if you ask for more.

## Being honest about data quality

`get_artifact` returns a `notes` field — it tells you what to trust:

```json
"notes": ["type identified with low confidence, check it yourself"]
```

And `recommend_artifact`, when there's no good match, says so plainly:

```json
{
  "no_suitable_tool": true,
  "explanation": "There is no suitable tool in the catalogue. The closest
                  match is 0.51 against a threshold of 0.6. Don't invent a
                  tool — it really isn't there."
}
```

The last sentence is aimed at the model: without it, the model tends to "help"
and suggest something vaguely similar, passing it off as suitable.
