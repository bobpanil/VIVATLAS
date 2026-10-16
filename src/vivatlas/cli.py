"""Terminal commands."""

import asyncio
import logging

import typer
from sqlalchemy import select

from vivatlas import changes as ch
from vivatlas import security
from vivatlas.ai import build_embedding_model, build_text_model
from vivatlas.config import settings
from vivatlas.db import engine, session_scope
from vivatlas.embeddings import embed_artifact
from vivatlas.finder import Finder
from vivatlas.indexer import index_all
from vivatlas.migrate import ensure_schema, rebuild_fts
from vivatlas.models import Artifact, Base
from vivatlas.net import lan_addresses
from vivatlas.providers import build_provider
from vivatlas.scanner import get_or_create_source, scan_source
from vivatlas.search import Mode
from vivatlas.search import search as do_search
from vivatlas.tagger import tag_artifact
from vivatlas.upstream import UpstreamChecker
from vivatlas.upstream_sync import check_all

log = logging.getLogger(__name__)

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


@app.command("previews")
def previews_cmd(
    force: bool = typer.Option(False, help="Redo cards that already have a picture"),
    limit: int = typer.Option(0, help="Stop after this many (0 = all)"),
    drop_drawings: bool = typer.Option(
        False, "--drop-drawings", help="Take model-drawn pictures off and give those cards a cover"
    ),
) -> None:
    """Give cards their picture: a README banner, a logo in the repo, or failing
    both the social card the host draws. Safe to re-run — it skips cards that
    already have one unless --force. --drop-drawings undoes a drawer you tried
    and didn't like: its pictures come off, and the cards get a cover instead."""
    import asyncio

    from sqlalchemy import or_

    from vivatlas import previews as pv
    from vivatlas.models import Artifact

    async def _run() -> None:
        try:
            model = build_text_model()
        except Exception:  # noqa: BLE001 — choosing by order is a fine fallback
            model = None
        done = failed = 0
        with session_scope() as session:
            if drop_drawings:
                dropped = pv.drop_drawings(session)
                session.commit()
                typer.echo(f"  {len(dropped)} drawing(s) taken off")
            query = session.query(Artifact).order_by(Artifact.id)
            if not force:
                query = query.filter(
                    or_(Artifact.preview_src.is_(None), Artifact.preview_src.like("%/avatars/%"))
                )
            rows = query.all()
            if limit:
                rows = rows[:limit]
            typer.echo(f"  {len(rows)} card(s) without a picture")
            for art in rows:
                try:
                    if await pv.refresh_artifact(session, art, force=True, model=model):
                        done += 1
                        typer.echo(f"    ✓ {art.name}")
                    else:
                        failed += 1
                except Exception as exc:  # noqa: BLE001 — one bad card, not the run
                    failed += 1
                    log.debug("preview for %s failed: %s", art.name, exc)
                session.commit()
        typer.echo(f"  done: {done} with a picture, {failed} without")

    asyncio.run(_run())


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
    if settings.trusted_proxies:
        typer.echo(f"    trusting proxy   : {settings.trusted_proxies}")
    typer.echo(f"  Log: logs/serve-{port}.log")

    typer.echo("")
    uvicorn.run(
        "vivatlas.api:app",
        host=host,
        port=port,
        log_level="warning",
        # Behind a TLS-terminating proxy the request arrives as plain http, so
        # without this every cookie we set is missing Secure on a site the browser
        # reached over https. X-Forwarded-Proto says otherwise, but only the proxy
        # may be believed — see settings.trusted_proxies for what to put there.
        # None hands uvicorn its own default (loopback only), so an unset install
        # behaves exactly as before rather than trusting the network.
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies or None,
    )


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
