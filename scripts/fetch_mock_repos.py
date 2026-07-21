"""Fetch real public GitHub repositories (themed to VivAtlas: skills, agents,
MCP servers, LLM tools) and cache them to scripts/mock_repos.json.

Run once with network + a GitHub token; the resulting JSON is bundled so the
seeder (scripts/seed_mock.py) works offline, including inside the container.

    python scripts/fetch_mock_repos.py            # token from secrets.md / env
    GITHUB_TOKEN=ghp_xxx python scripts/fetch_mock_repos.py
"""

import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "scripts" / "mock_repos.json"
TARGET = 200

# Themed queries — for the "skills, agents, tools" catalogue. We pull
# notable (by stars) public repositories from different corners of the topic.
QUERIES = [
    "topic:mcp",
    "topic:model-context-protocol",
    "topic:ai-agents",
    "topic:llm",
    "topic:claude",
    "topic:langchain",
    "topic:rag",
    "topic:prompt-engineering",
    "topic:agents",
    "topic:openai",
    "topic:chatbot",
    "topic:vector-database",
]


def token() -> str:
    t = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if t:
        return t.strip()
    secrets = ROOT / "secrets.md"
    if secrets.exists():
        m = re.search(r"ghp_[A-Za-z0-9]+", secrets.read_text(encoding="utf-8", errors="ignore"))
        if m:
            return m.group(0)
    return ""


def main() -> int:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = token()
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
        print("using GitHub token")
    else:
        print("no token — unauthenticated (low rate limit)")

    seen: dict[str, dict] = {}
    with httpx.Client(timeout=30, headers=headers) as client:
        for q in QUERIES:
            if len(seen) >= TARGET:
                break
            r = client.get(
                "https://api.github.com/search/repositories",
                params={"q": f"{q} stars:>50", "sort": "stars", "order": "desc", "per_page": 50},
            )
            if r.status_code != 200:
                print(f"  {q!r}: HTTP {r.status_code} {r.text[:120]}")
                continue
            items = r.json().get("items", [])
            fresh = 0
            for it in items:
                fn = it["full_name"]
                if fn in seen:
                    continue
                seen[fn] = {
                    "external_id": str(it["id"]),
                    "full_name": fn,
                    "owner": it["owner"]["login"],
                    "name": it["name"],
                    "description": it.get("description") or "",
                    "html_url": it["html_url"],
                    "clone_url": it["clone_url"],
                    "language": it.get("language") or "",
                    "topics": it.get("topics") or [],
                    "stars": it.get("stargazers_count") or 0,
                    "created_at": it.get("created_at") or "",
                    "updated_at": it.get("updated_at") or "",
                    "pushed_at": it.get("pushed_at") or "",
                    "default_branch": it.get("default_branch") or "main",
                    "size_kb": it.get("size") or 0,
                }
                fresh += 1
            print(f"  {q!r}: +{fresh} (total {len(seen)})")
            # Search API: go easy on the rate limit (30/min when authenticated).
            time.sleep(2)

    repos = list(seen.values())[:TARGET]
    OUT.write_text(json.dumps(repos, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nwrote {len(repos)} repos to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
