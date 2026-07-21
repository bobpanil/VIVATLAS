"""Seed the catalog with mock cards built from real GitHub repositories.

Reads scripts/mock_repos.json (bundled, produced by fetch_mock_repos.py) and
creates one shared/common card per repo — Source -> Repository -> Artifact,
with tags and full-text index. No network needed.

    python scripts/seed_mock.py            # add cards (skips repos already seeded)
    python scripts/seed_mock.py --wipe     # remove the demo source first, then seed

The demo data lives under a single Source (kind="github", the demo base_url), so
--wipe only ever touches mock cards, never anything a real person added.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import select, text  # noqa: E402

from vivatlas.db import session_scope  # noqa: E402
from vivatlas.models import (  # noqa: E402
    Artifact,
    ArtifactCategory,
    ArtifactTag,
    Change,
    Embedding,
    Favorite,
    Repository,
    Source,
    Tag,
    TagSuppression,
    UpstreamLink,
)
from vivatlas.search import index_artifact_for_words  # noqa: E402

DEMO_BASE_URL = "https://github.com/__vivatlas_demo__"
DEMO_NAME = "GitHub (demo data)"
DATA = ROOT / "scripts" / "mock_repos.json"

# --- tagging ----------------------------------------------------------------
PLATFORM = {"anthropic", "openai", "claude", "gpt", "gemini", "ollama",
            "huggingface", "langchain", "llamaindex", "azure", "aws", "google", "mistral"}
PURPOSE = {"ai", "ai-agents", "agents", "agent", "agentic-ai", "rag", "chatbot",
           "automation", "prompt-engineering", "vector-database", "llm", "llms",
           "nlp", "machine-learning", "deep-learning", "embeddings", "search",
           "workflow", "assistant", "copilot", "generative-ai"}
FORMAT = {"mcp", "model-context-protocol", "api", "sdk", "cli", "rest",
          "graphql", "plugin", "tool", "tools", "framework"}
RUN = {"docker", "kubernetes", "serverless", "self-hosted", "web", "desktop"}


def pick_type(topics: set[str], text_blob: str) -> str:
    def has(*words: str) -> bool:
        return any(w in topics for w in words) or any(w in text_blob for w in words)

    if has("mcp", "model-context-protocol"):
        return "mcp-server"
    if has("claude") and has("skill", "skills"):
        return "claude-skill"
    if has("agent", "agents", "ai-agents", "agentic-ai", "autonomous"):
        return "claude-agent"
    if has("prompt", "prompts", "prompt-engineering", "command", "commands"):
        return "claude-command"
    if has("plugin", "extension", "vscode", "obsidian"):
        return "plugin"
    if has("design", "ui", "css", "component", "tailwind", "frontend"):
        return "design-kit"
    if has("sdk", "library", "framework", "skill"):
        return "skill"
    return "project"


def tag_category(topic: str) -> str:
    if topic in PLATFORM:
        return "platform"
    if topic in FORMAT:
        return "format"
    if topic in RUN:
        return "runtime"
    if topic in PURPOSE:
        return "purpose"
    return "other"


def parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def get_or_create_source(session) -> Source:
    src = session.scalar(select(Source).where(Source.base_url == DEMO_BASE_URL))
    if src is None:
        src = Source(kind="github", display_name=DEMO_NAME, base_url=DEMO_BASE_URL,
                     enabled=True, owner_user_id=None)
        session.add(src)
        session.flush()
    return src


def get_or_create_tag(session, cache: dict, slug: str, category: str) -> Tag:
    slug = slug.strip().lower()[:64]
    if slug in cache:
        return cache[slug]
    tag = session.scalar(select(Tag).where(Tag.slug == slug))
    if tag is None:
        tag = Tag(slug=slug, label=slug.replace("-", " "), category=category)
        session.add(tag)
        session.flush()
    cache[slug] = tag
    return tag


def wipe(session) -> int:
    src = session.scalar(select(Source).where(Source.base_url == DEMO_BASE_URL))
    if src is None:
        return 0
    repo_ids = list(session.scalars(select(Repository.id).where(Repository.source_id == src.id)))
    art_ids = list(
        session.scalars(select(Artifact.id).where(Artifact.repository_id.in_(repo_ids)))
    ) if repo_ids else []
    if art_ids:
        for model in (ArtifactTag, ArtifactCategory, Favorite, Embedding,
                      UpstreamLink, TagSuppression, Change):
            session.execute(model.__table__.delete().where(model.artifact_id.in_(art_ids)))
        session.execute(Artifact.__table__.delete().where(Artifact.id.in_(art_ids)))
        # FTS — its own table, clean up by rowid.
        ids_csv = ",".join(str(i) for i in art_ids)
        session.execute(text(f"DELETE FROM artifacts_fts WHERE rowid IN ({ids_csv})"))
    if repo_ids:
        session.execute(Repository.__table__.delete().where(Repository.id.in_(repo_ids)))
    session.execute(Source.__table__.delete().where(Source.id == src.id))
    return len(art_ids)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="remove demo source before seeding")
    args = ap.parse_args()

    repos = json.loads(DATA.read_text(encoding="utf-8"))
    added = skipped = 0
    with session_scope() as session:
        if args.wipe:
            n = wipe(session)
            print(f"wiped {n} demo cards")
        src = get_or_create_source(session)
        tag_cache: dict[str, Tag] = {}

        existing = set(
            session.scalars(
                select(Repository.external_id).where(Repository.source_id == src.id)
            )
        )

        for i, r in enumerate(repos):
            if r["external_id"] in existing:
                skipped += 1
                continue

            topics = {t.lower() for t in r.get("topics", [])}
            blob = f"{r['name']} {r['description']}".lower()
            atype = pick_type(topics, blob)
            desc = r["description"] or f"{r['full_name']} — {r['language'] or 'code'} repository."
            stars = r.get("stars", 0)
            lang = r.get("language", "")

            repo = Repository(
                source_id=src.id,
                external_id=r["external_id"],
                owner=r["owner"],
                name=r["name"],
                default_branch=r.get("default_branch", "main"),
                description=desc,
                html_url=r["html_url"],
                clone_url=r["clone_url"],
                original_url=r["html_url"],
                size_kb=r.get("size_kb", 0),
                remote_created_at=parse_dt(r.get("created_at", "")),
                remote_updated_at=parse_dt(r.get("updated_at") or r.get("pushed_at", "")),
            )
            session.add(repo)
            session.flush()

            short = desc if len(desc) <= 160 else desc[:157] + "…"
            topic_str = ", ".join(sorted(topics)[:6])
            art = Artifact(
                repository_id=repo.id,
                name=r["name"],
                artifact_type=atype,
                confidence=0.72 + (i % 20) / 100.0,
                detect_reasons=f"topics: {topic_str}" if topic_str else "seeded",
                shared=True,
                hidden=False,
                is_new=(i % 17 == 0),
                owner_user_id=None,
                doc_text=f"{r['full_name']} {desc} {' '.join(sorted(topics))} {lang}".strip(),
                summary_short=short,
                summary_normal=(
                    f"{desc} {('★' + format(stars, ',')) if stars else ''}"
                    f"{(' · ' + lang) if lang else ''}".strip()
                ),
                summary_technical=(
                    f"{r['full_name']} — {lang or 'multi-language'}, {stars:,} stars. "
                    f"{('Topics: ' + topic_str + '. ') if topic_str else ''}{desc}"
                ),
                summary_model="seed",
                file_count=0,
                file_paths="[]",
            )
            session.add(art)
            session.flush()

            # Tags: language (rule) + up to 4 topics (platform/format topic — rule,
            # other/purpose — "model"). A couple are manual, so the legend shows
            # all three sources. used — so the same tag (language and
            # topic can coincide, e.g. Go and topic:go) doesn't land on the card twice.
            used: set[int] = set()
            if lang:
                t = get_or_create_tag(session, tag_cache, lang, "language")
                if t.id not in used:
                    used.add(t.id)
                    session.add(ArtifactTag(artifact_id=art.id, tag_id=t.id, source="derived",
                                            confidence=1.0, origin="language"))
            for j, topic in enumerate(sorted(topics)[:4]):
                cat = tag_category(topic)
                src_kind = "derived" if cat in ("platform", "format", "runtime") else "ai"
                if i % 13 == 0 and j == 0:
                    src_kind = "manual"
                t = get_or_create_tag(session, tag_cache, topic, cat)
                if t.id in used:
                    continue
                used.add(t.id)
                session.add(ArtifactTag(
                    artifact_id=art.id, tag_id=t.id, source=src_kind,
                    confidence=1.0 if src_kind != "ai" else 0.8,
                    origin="topic" if src_kind != "ai" else "model",
                    manually_confirmed=(src_kind == "manual"),
                ))

            index_artifact_for_words(session, art)
            added += 1

    print(f"seeded: +{added} cards, skipped {skipped} already present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
