# MCP access (for AI assistants)

VIVATLAS answers not only you through the browser, but also any AI assistant that
speaks the [Model Context Protocol](https://modelcontextprotocol.io). Ask your
assistant "what do I have for building a landing page?" — it digs into your catalogue
and answers from facts, instead of making things up.

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

## Local (stdio)

For a client that launches the server as a local process. Add a server to your MCP
client's config (the exact file depends on the client), pointing at the CLI:

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

Then ask your assistant "show me an overview of my tool catalogue" to check it.

## Remote (HTTP)

For a client that connects over the network. Serve on a reachable address (behind a
reverse proxy or tunnel that terminates TLS):

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
