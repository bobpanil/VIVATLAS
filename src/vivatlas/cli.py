"""Terminal commands."""

import asyncio
import logging

import typer
from sqlalchemy import select

from vivatlas import changes as ch
from vivatlas import remap, security
from vivatlas.ai import build_embedding_model, build_text_model
from vivatlas.config import settings
from vivatlas.db import engine, session_scope
from vivatlas.embeddings import embed_artifact
from vivatlas.finder import Finder
from vivatlas.import_run import execute, record_upstream
from vivatlas.importer import GitHubFetcher, ImportError_, plan_import
from vivatlas.indexer import index_all, index_repository
from vivatlas.migrate import ensure_schema, rebuild_fts
from vivatlas.models import Artifact, Base, Repository, UpstreamLink
from vivatlas.net import lan_addresses
from vivatlas.providers import build_provider
from vivatlas.scanner import get_or_create_source, scan_source
from vivatlas.search import Mode, index_artifact_for_words
from vivatlas.search import search as do_search
from vivatlas.tagger import tag_artifact
from vivatlas.updater import UpdateRefused, apply_update, plan_update
from vivatlas.upstream import UpstreamChecker
from vivatlas.upstream_sync import check_all

app = typer.Typer(help="VIVATLAS")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@app.command("init-db")
def init_db() -> None:
    """Create or update the tables."""
    Base.metadata.create_all(engine)
    for step in ensure_schema():
        typer.echo(f"  {step}")
    typer.echo(f"Database ready: {settings.database_url}")


@app.command("embed")
def embed(force: bool = typer.Option(False, help="Recompute everything")) -> None:
    """Turn cards into numbers for meaning-based search."""

    async def _run() -> None:
        model = build_embedding_model()
        created = unchanged = failed = 0
        try:
            with session_scope() as session:
                arts = session.scalars(select(Artifact)).all()
                for i, art in enumerate(arts, 1):
                    try:
                        outcome = await embed_artifact(session, model, art, force=force)
                        session.commit()
                        if outcome == "unchanged":
                            unchanged += 1
                        else:
                            created += 1
                        if i % 20 == 0:
                            typer.echo(f"  {i}/{len(arts)}")
                    except Exception as exc:
                        session.rollback()
                        failed += 1
                        typer.echo(f"  {art.name}: ERROR {exc}")
                    await asyncio.sleep(settings.llm_delay_seconds)
        finally:
            await model.aclose()

        typer.echo("")
        typer.echo(f"  Computed      : {created}")
        typer.echo(f"  Unchanged     : {unchanged}")
        typer.echo(f"  Errors        : {failed}")

    asyncio.run(_run())


@app.command("tag")
def tag(no_ai: bool = typer.Option(False, help="Rule-based tags only")) -> None:
    """Assign tags."""

    async def _run() -> None:
        model = None if no_ai else build_text_model()
        totals = {"derived": 0, "ai": 0, "rejected": 0, "weak": 0}
        failed = 0
        try:
            with session_scope() as session:
                arts = session.scalars(select(Artifact)).all()
                for i, art in enumerate(arts, 1):
                    try:
                        stats = await tag_artifact(session, art, model)
                        session.commit()
                        for k, v in stats.items():
                            totals[k] += v
                        if i % 20 == 0:
                            typer.echo(f"  {i}/{len(arts)}")
                    except Exception as exc:
                        session.rollback()
                        failed += 1
                        typer.echo(f"  {art.name}: ERROR {exc}")
                    if model:
                        await asyncio.sleep(settings.llm_delay_seconds)
        finally:
            if model:
                await model.aclose()

        typer.echo("")
        typer.echo(f"  By rules           : {totals['derived']}")
        typer.echo(f"  From model         : {totals['ai']}")
        typer.echo(f"  Rejected (blocked) : {totals['rejected']}")
        typer.echo(f"  Weak (not applied) : {totals['weak']}")
        typer.echo(f"  Errors             : {failed}")

    asyncio.run(_run())


@app.command("find")
def find_cmd(source: str) -> None:
    """Find a repository from anything: a link, page, screenshot, or clip.

    Doesn't pull or create anything — it just shows what turned up. You choose:
    a name heard aloud or read from an image is recognized imprecisely, and a
    mistake is costly. From there it's a plain import from the ready-made line.

        vivatlas find https://github.com/DeusData/codebase-memory-mcp
        vivatlas find https://voltagent.dev/
        vivatlas find C:/screenshots/reel.png
        vivatlas find "a skill that gathers news from the last 30 days"
    """

    async def _run() -> None:
        finder = Finder(github_token=settings.github_token)
        model = build_text_model() if settings.google_api_key else None
        try:
            result = await finder.find(source, model)
        finally:
            await finder.aclose()
            if model is not None:
                await model.aclose()

        kinds = {
            "github": "a GitHub link",
            "web": "a web page",
            "image": "an image",
            "video": "a clip",
            "text": "words",
        }
        typer.echo("")
        typer.echo(f"  You gave: {kinds.get(result.kind, result.kind)}")
        if result.heard:
            lang = f" ({result.language})" if result.language else ""
            typer.echo(f"  Read{lang}: {result.heard[:150]}")
        if result.gist:
            typer.echo(f"  About: {result.gist[:150]}")
        if result.tool_name:
            typer.echo(f"  Name: {result.tool_name}")
        for note in result.notes:
            typer.echo(f"    · {note}")

        if not result.candidates:
            typer.echo("")
            typer.echo("  Nothing found. Try giving a link directly.")
            return

        typer.echo("")
        typer.echo(f"  FOUND: {len(result.candidates)}")
        for i, c in enumerate(result.candidates, 1):
            mark = "exact" if c.exact else "close"
            typer.echo(f"    {i}. [{mark}] {c.repo} — {c.stars:,} stars".replace(",", " "))
            if c.description:
                typer.echo(f"       {c.description}")
            typer.echo(f"       {c.why}")

        typer.echo("")
        typer.echo("  Picked one — pull it:")
        typer.echo(f"    vivatlas import {result.candidates[0].url}")

    asyncio.run(_run())


@app.command("import")
def import_cmd(
    url: str,
    to: str = typer.Option("", help="Owner in Gitea. Empty — same as on GitHub."),
    name: str = typer.Option("", help="Local name (defaults to the source's)"),
    yes: bool = typer.Option(False, "--yes", help="Execute. Without this, only shows the plan."),
) -> None:
    """Pull in a tool from a GitHub link.

    Without --yes, only shows what will be done. Nothing is created.
    """

    async def _run() -> None:
        fetcher = GitHubFetcher(token=settings.github_token)
        try:
            plan = await plan_import(fetcher, url, target_owner=to, target_name=name)
        except ImportError_ as exc:
            typer.echo(str(exc))
            raise typer.Exit(1) from None
        finally:
            await fetcher.aclose()

        typer.echo("")
        typer.echo("  WHAT WILL BE DONE")
        typer.echo(
            f"    from    : github.com/{plan.source.full_repo}"
            + (f"/{plan.source.path}" if plan.source.path else "")
        )
        typer.echo(f"    creates : {plan.target_owner}/{plan.target_name}")
        typer.echo(f"    files   : {len(plan.files)}, {plan.total_bytes / 1024:.0f} KB")
        for f in plan.files[:5]:
            typer.echo(f"       {f.path}")
        if len(plan.files) > 5:
            typer.echo(f"       ... {len(plan.files) - 5} more")
        for w in plan.warnings:
            typer.echo(f"    ! {w}")

        if not yes:
            typer.echo("")
            typer.echo("  Nothing done. Repeat with --yes to execute.")
            return

        if not settings.gitea_token:
            typer.echo("")
            typer.echo("  No GITEA_TOKEN — nothing to write with. Add the token to .env")
            raise typer.Exit(1)

        provider = build_provider("gitea")
        text_model = build_text_model()
        embed_model = build_embedding_model()
        try:
            with session_scope() as session:
                result = await execute(session, provider, plan, settings.gitea_url)
                session.commit()
                typer.echo("")
                typer.echo(f"  Created: {result.repo_full_name}, files {result.files_written}")

                row = session.get(Repository, result.repository_id)
                await index_repository(session, provider, text_model, row, force=True)
                session.commit()

                art = session.scalar(select(Artifact).where(Artifact.repository_id == row.id))
                record_upstream(session, art.id, plan)
                await embed_artifact(session, embed_model, art)
                await tag_artifact(session, art, text_model)
                index_artifact_for_words(session, art)
                session.commit()

                typer.echo(f"  Card: {art.name} [{art.artifact_type}]")
                typer.echo(f"  Description: {art.summary_short[:70]}")
                typer.echo(f"  Source recorded: {plan.source.full_repo}")
        finally:
            await provider.aclose()
            await text_model.aclose()
            await embed_model.aclose()

    asyncio.run(_run())


@app.command("remap")
def remap_cmd(
    yes: bool = typer.Option(False, "--yes", help="Execute. Without this, only shows the plan."),
    limit: int = typer.Option(0, help="Move at most this many (0 — all). For testing."),
) -> None:
    """Rename repositories by the "path as on GitHub" rule.

    Without --yes, only shows what will move and where. Touches nothing.

    Moves them one at a time and stops at the very first error: a half-done
    move is a state from which you can see where it got stuck.
    """

    async def _run() -> None:
        with session_scope() as session:
            plan = remap.compute_plan(session)

            typer.echo("")
            typer.echo(f"  To rename      : {len(plan.changes)}")
            typer.echo(f"  Leave alone    : {len(plan.unchanged)} (source not recorded)")
            typer.echo(f"  Already correct: {len(plan.already)}")
            if plan.new_orgs:
                typer.echo(f"  Create organizations: {', '.join(plan.new_orgs)}")

            typer.echo("")
            shown = plan.changes if yes else plan.changes[:12]
            for item in shown:
                typer.echo(f"    {item.old_full}  ->  {item.new_full}")
            if not yes and len(plan.changes) > 12:
                typer.echo(f"    ... {len(plan.changes) - 12} more")

            if not plan.changes:
                typer.echo("\n  Nothing to move.")
                return

            if not yes:
                typer.echo("")
                typer.echo("  Nothing done. Repeat with --yes to execute.")
                return

            if not settings.gitea_token:
                typer.echo("\n  No GITEA_TOKEN — nothing to write with.")
                raise typer.Exit(1)

            provider = build_provider("gitea")
            items = plan.changes[:limit] if limit else plan.changes
            done = 0
            try:
                for i, item in enumerate(items, 1):
                    try:
                        await remap.apply_item(session, provider, item, settings.gitea_url)
                        session.commit()
                        done += 1
                        typer.echo(f"  [{i}/{len(items)}] {item.old_full} -> {item.new_full}")
                    except Exception as exc:
                        session.rollback()
                        typer.echo("")
                        typer.echo(f"  STOPPED at {item.old_full}: {exc}")
                        typer.echo(f"  Moved successfully: {done}. The rest untouched.")
                        raise typer.Exit(1) from None
            finally:
                await provider.aclose()

            typer.echo("")
            typer.echo(f"  Done: moved {done}.")

    asyncio.run(_run())


@app.command("update")
def update_cmd(
    name: str = typer.Argument("", help="Card name. Empty — all with a new version out."),
    yes: bool = typer.Option(False, "--yes", help="Execute. Without this, only shows the plan."),
) -> None:
    """Install the new version from the source in place of the old one.

    Updates only what you haven't touched. If the copy was edited, it refuses
    and says why: an overwrite would silently wipe out your change.

    Without --yes, only shows what will be done.
    """

    async def _run() -> None:
        provider = build_provider("gitea")
        checker = UpstreamChecker(token=settings.github_token)
        text_model = build_text_model() if settings.google_api_key else None
        embed_model = build_embedding_model() if settings.google_api_key else None
        try:
            with session_scope() as session:
                q = select(UpstreamLink).join(Artifact)
                if name:
                    q = q.where(Artifact.name == name)
                else:
                    q = q.where(UpstreamLink.status == "update-available")
                links = list(session.scalars(q).all())

                if not links:
                    typer.echo("")
                    if name:
                        typer.echo(f"  Card \"{name}\" not found or has no recorded source.")
                    else:
                        typer.echo("  Nothing to update. First: vivatlas upstream")
                    return

                plans, refused = [], []
                for link in links:
                    try:
                        plans.append(await plan_update(session, provider, checker, link))
                    except UpdateRefused as exc:
                        refused.append((link.artifact.name, str(exc)))
                    except Exception as exc:
                        refused.append((link.artifact.name, f"check failed: {exc}"))

                typer.echo("")
                for who, why in refused:
                    typer.echo(f"  — {who}: {why}")

                if not plans:
                    return

                typer.echo("")
                typer.echo("  WHAT WILL BE REPLACED")
                for p in plans:
                    typer.echo(f"    {p.repo_full_name} · {p.path} ({p.size_kb:.0f} KB)")
                    typer.echo(f"       from: github.com/{p.upstream_repo}/{p.upstream_path}")
                    typer.echo(f"       was {p.old_sha[:8]} -> becomes {p.new_sha[:8]}")

                if not yes:
                    typer.echo("")
                    typer.echo("  Nothing done. Repeat with --yes to execute.")
                    return

                if not settings.gitea_token:
                    typer.echo("")
                    typer.echo("  No GITEA_TOKEN — nothing to write with. Add the token to .env")
                    raise typer.Exit(1)

                done = 0
                for p in plans:
                    link = session.get(UpstreamLink, p.link_id)
                    try:
                        await apply_update(session, provider, checker, p)
                        session.commit()
                        done += 1
                        typer.echo(f"  Done: {p.repo_full_name} · {p.path}")
                    except Exception as exc:
                        session.rollback()
                        typer.echo(f"  ERROR: {p.repo_full_name}: {exc}")
                        continue

                    # The file changed — so the description, tags, and search are stale.
                    if text_model is None or embed_model is None:
                        typer.echo("     card not rebuilt: no GOOGLE_API_KEY")
                        continue
                    try:
                        repo = link.artifact.repository
                        await index_repository(session, provider, text_model, repo, force=True)
                        session.commit()
                        art = session.scalar(
                            select(Artifact).where(Artifact.repository_id == repo.id)
                        )
                        await embed_artifact(session, embed_model, art)
                        await tag_artifact(session, art, text_model)
                        index_artifact_for_words(session, art)
                        session.commit()
                        typer.echo(f"     card rebuilt: {art.summary_short[:60]}")
                    except Exception as exc:
                        session.rollback()
                        typer.echo(f"     file updated, but the card wasn't rebuilt: {exc}")

                typer.echo("")
                typer.echo(f"  Updated: {done} of {len(plans)}")
        finally:
            await provider.aclose()
            await checker.aclose()
            if text_model is not None:
                await text_model.aclose()
            if embed_model is not None:
                await embed_model.aclose()

    asyncio.run(_run())


@app.command("changes")
def changes_cmd(
    days: int = typer.Option(30, help="Over how many days"),
    stale: bool = typer.Option(False, help="Show stale items"),
) -> None:
    """What appeared, changed, vanished, and what's gone stale."""
    with session_scope() as session:
        if stale:
            items = ch.stale(session)
            oldest, newest = ch.oldest_and_newest(session)
            if not items:
                typer.echo(f"Nothing has been stale longer than {ch.STALE_DAYS} days.")
                typer.echo(f"Oldest: {oldest} days, newest: {newest} days.")
                return
            for it in items:
                typer.echo(f"  !  {it.artifact.repository.full_name:44s} {it.reason}")
            return

        events = ch.since(session, days=days)
        if not events:
            typer.echo(f"Nothing happened over {days} days.")
            return
        for c in events:
            mark = ch.KIND_MARKS.get(c.kind, "·")
            when = c.created_at.strftime("%d.%m %H:%M")
            typer.echo(f"  {mark}  {when}  {c.title:40s} {c.details[:50]}")
        typer.echo("")
        for k, n in ch.summary(session, days=days).items():
            typer.echo(f"  {ch.KIND_NAMES.get(k, k)}: {n}")


@app.command("upstream")
def upstream_cmd() -> None:
    """Check whether sources have new versions out."""

    async def _run() -> None:
        provider = build_provider("gitea")
        checker = UpstreamChecker(token=settings.github_token)
        try:
            with session_scope() as session:
                result = await check_all(session, provider, checker)
        finally:
            await provider.aclose()
            await checker.aclose()

        typer.echo("")
        typer.echo(f"  Checked              : {result.checked}")
        typer.echo(f"  In sync with source  : {result.in_sync}")
        typer.echo(f"  NEW VERSION OUT      : {result.update_available}")
        typer.echo(f"  You edited           : {result.locally_modified}")
        typer.echo(f"  Diverged both ways   : {result.diverged}")
        typer.echo(f"  Errors               : {result.failed}")

    asyncio.run(_run())


@app.command("secret")
def secret_cmd() -> None:
    """Generate the secret key for .env.

    The whole lock rests on it: signatures and encryption of others' tokens.
    Changing it later signs everyone out and loses saved tokens.
    """
    typer.echo("")
    typer.echo("  Add to .env as a single line:")
    typer.echo("")
    typer.echo(f"  SECRET_KEY={security.new_token(48)}")
    typer.echo("")
    typer.echo("  Don't show it to anyone and don't commit it to Git (.env is ignored anyway).")


@app.command("serve")
def serve(
    port: int = typer.Option(8000),
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 — to open it from a phone"),
) -> None:
    """Start the web interface."""
    import uvicorn

    # Without the secret key the lock won't close. We check here, not when the
    # user hits "Sign in": trouble should surface at startup.
    try:
        security.require_secret()
    except security.SecretMissing as exc:
        typer.echo("")
        typer.echo(f"  {exc}")
        raise typer.Exit(1) from None

    # We print the addresses here, not in the launcher file: this window stays
    # open while the server runs, whereas the launcher window closes right away.
    # For someone who started it with a double-click, the phone address shows only here.
    typer.echo("")
    typer.echo(f"  VIVATLAS, port {port}")
    typer.echo(f"    on this computer : http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        for ip in lan_addresses():
            typer.echo(f"    from a phone     : http://{ip}:{port}")
        typer.echo("")
        typer.echo("  The phone must be on the same network.")

    # The program's own log (not uvicorn's request handler) goes to a file. It
    # shows what's going on inside: whether 2FA kicked in, whether a write rolled back.
    import pathlib

    logdir = pathlib.Path("logs")
    logdir.mkdir(exist_ok=True)
    handler = logging.FileHandler(logdir / f"serve-{port}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app_log = logging.getLogger("vivatlas")
    app_log.addHandler(handler)
    app_log.setLevel(logging.INFO)
    typer.echo(f"  Log: logs/serve-{port}.log")

    typer.echo("")
    uvicorn.run("vivatlas.api:app", host=host, port=port, log_level="warning")


@app.command("mcp")
def mcp_stdio() -> None:
    """Start the MCP server over stdio (for a local MCP client)."""
    # We silence the logs: in stdio mode stdout is the protocol channel, any line
    # in it breaks the connection.
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    from vivatlas.mcp_server import run_stdio

    run_stdio()


@app.command("reindex-words")
def reindex_words() -> None:
    """Rebuild the word-search table."""
    count = rebuild_fts()
    typer.echo(f"In word search: {count} cards")


@app.command("search")
def search_cmd(
    query: str,
    mode: str = typer.Option("both", help="words | meaning | both"),
    limit: int = typer.Option(5),
) -> None:
    """Find a tool."""

    async def _run() -> None:
        model = build_embedding_model() if mode in ("meaning", "both") else None
        try:
            with session_scope() as session:
                hits = await do_search(session, query, model, mode=Mode(mode), limit=limit)
                if not hits:
                    typer.echo("Nothing found")
                    return
                for i, h in enumerate(hits, 1):
                    a = h.artifact
                    typer.echo(f"\n{i}. {a.repository.full_name}  [{a.artifact_type}]")
                    typer.echo(f"   {a.summary_short}")
                    typer.echo(f"   why: {', '.join(h.reasons)}   score: {h.score:.4f}")
        finally:
            if model:
                await model.aclose()

    asyncio.run(_run())


@app.command("scan")
def scan() -> None:
    """Fetch the list of repositories from Gitea."""

    async def _run() -> None:
        provider = build_provider("gitea")
        try:
            with session_scope() as session:
                source = get_or_create_source(session, "gitea", settings.gitea_url, "Gitea")
                result = await scan_source(session, provider, source)
        finally:
            await provider.aclose()

        typer.echo("")
        typer.echo(f"  Found on host     : {result.seen}")
        typer.echo(f"  Skipped private   : {result.skipped_private}")
        typer.echo(f"  New added         : {result.added}")
        typer.echo(f"  Updated           : {result.updated}")
        typer.echo(f"  Vanished          : {result.gone}")
        typer.echo(f"  Total in database : {result.stored}")

    asyncio.run(_run())


@app.command("index")
def index(
    limit: int = typer.Option(None, help="Process only the first N repositories"),
    force: bool = typer.Option(False, help="Rebuild even if the commit hasn't changed"),
    no_ai: bool = typer.Option(False, help="No descriptions — only detect the type"),
) -> None:
    """Build cards: download repositories, detect, describe."""

    async def _run() -> None:
        provider = build_provider("gitea")
        text_model = None if no_ai else build_text_model()
        try:
            with session_scope() as session:
                result = await index_all(
                    session,
                    provider,
                    text_model,
                    delay=settings.llm_delay_seconds if text_model else 0.0,
                    limit=limit,
                    force=force,
                )
        finally:
            await provider.aclose()
            if text_model:
                await text_model.aclose()

        typer.echo("")
        typer.echo(f"  Processed         : {result.processed}")
        typer.echo(f"  New cards         : {result.created}")
        typer.echo(f"  Updated           : {result.updated}")
        typer.echo(f"  Unchanged         : {result.unchanged}")
        typer.echo(f"  With description  : {result.summarized}")
        typer.echo(f"  Description failed: {result.summary_failed}")
        typer.echo(f"  Errors            : {result.failed}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
