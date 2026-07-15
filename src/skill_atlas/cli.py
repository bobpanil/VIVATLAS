"""Команды в терминале."""

import asyncio
import logging

import typer
from sqlalchemy import select

from skill_atlas.ai import build_embedding_model, build_text_model
from skill_atlas.config import settings
from skill_atlas.db import engine, session_scope
from skill_atlas.embeddings import embed_artifact
from skill_atlas.indexer import index_all
from skill_atlas.migrate import ensure_schema, rebuild_fts
from skill_atlas.models import Artifact, Base
from skill_atlas.providers import build_provider
from skill_atlas.scanner import get_or_create_source, scan_source
from skill_atlas.search import Mode
from skill_atlas.search import search as do_search
from skill_atlas.tagger import tag_artifact
from skill_atlas.upstream import UpstreamChecker
from skill_atlas.upstream_sync import check_all

app = typer.Typer(help="Skill Atlas")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@app.command("init-db")
def init_db() -> None:
    """Создать или обновить таблицы."""
    Base.metadata.create_all(engine)
    for step in ensure_schema():
        typer.echo(f"  {step}")
    typer.echo(f"База готова: {settings.database_url}")


@app.command("embed")
def embed(force: bool = typer.Option(False, help="Пересчитать все")) -> None:
    """Превратить карточки в числа для поиска по смыслу."""

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
                        typer.echo(f"  {art.name}: ОШИБКА {exc}")
                    await asyncio.sleep(settings.llm_delay_seconds)
        finally:
            await model.aclose()

        typer.echo("")
        typer.echo(f"  Посчитано     : {created}")
        typer.echo(f"  Не изменилось : {unchanged}")
        typer.echo(f"  Ошибок        : {failed}")

    asyncio.run(_run())


@app.command("tag")
def tag(no_ai: bool = typer.Option(False, help="Только теги по правилам")) -> None:
    """Расставить теги."""

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
                        typer.echo(f"  {art.name}: ОШИБКА {exc}")
                    if model:
                        await asyncio.sleep(settings.llm_delay_seconds)
        finally:
            if model:
                await model.aclose()

        typer.echo("")
        typer.echo(f"  По правилам        : {totals['derived']}")
        typer.echo(f"  От модели          : {totals['ai']}")
        typer.echo(f"  Отклонено запретом : {totals['rejected']}")
        typer.echo(f"  Слабых (не ставим) : {totals['weak']}")
        typer.echo(f"  Ошибок             : {failed}")

    asyncio.run(_run())


@app.command("upstream")
def upstream_cmd() -> None:
    """Проверить, не вышли ли новые версии у источников."""

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
        typer.echo(f"  Проверено              : {result.checked}")
        typer.echo(f"  Совпадает с источником : {result.in_sync}")
        typer.echo(f"  ВЫШЛА НОВАЯ ВЕРСИЯ     : {result.update_available}")
        typer.echo(f"  Вы правили             : {result.locally_modified}")
        typer.echo(f"  Разошлось с обеих      : {result.diverged}")
        typer.echo(f"  Ошибок                 : {result.failed}")

    asyncio.run(_run())


@app.command("serve")
def serve(
    port: int = typer.Option(8000),
    host: str = typer.Option("127.0.0.1", help="0.0.0.0 — чтобы открыть с телефона"),
) -> None:
    """Запустить веб-интерфейс."""
    import uvicorn

    typer.echo(f"Открой: http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    uvicorn.run("skill_atlas.api:app", host=host, port=port, log_level="warning")


@app.command("mcp")
def mcp_stdio() -> None:
    """Запустить MCP-сервер для Claude Code (stdio)."""
    # Логи глушим: в stdio-режиме stdout это канал протокола, любая строчка
    # в нём ломает связь.
    logging.getLogger().handlers.clear()
    logging.getLogger().addHandler(logging.NullHandler())
    from skill_atlas.mcp_server import run_stdio

    run_stdio()


@app.command("reindex-words")
def reindex_words() -> None:
    """Пересобрать таблицу поиска по словам."""
    count = rebuild_fts()
    typer.echo(f"В поиске по словам: {count} карточек")


@app.command("search")
def search_cmd(
    query: str,
    mode: str = typer.Option("both", help="words | meaning | both"),
    limit: int = typer.Option(5),
) -> None:
    """Найти инструмент."""

    async def _run() -> None:
        model = build_embedding_model() if mode in ("meaning", "both") else None
        try:
            with session_scope() as session:
                hits = await do_search(session, query, model, mode=Mode(mode), limit=limit)
                if not hits:
                    typer.echo("Ничего не нашлось")
                    return
                for i, h in enumerate(hits, 1):
                    a = h.artifact
                    typer.echo(f"\n{i}. {a.repository.full_name}  [{a.artifact_type}]")
                    typer.echo(f"   {a.summary_short}")
                    typer.echo(f"   почему: {', '.join(h.reasons)}   оценка: {h.score:.4f}")
        finally:
            if model:
                await model.aclose()

    asyncio.run(_run())


@app.command("scan")
def scan() -> None:
    """Забрать список репозиториев из Gitea."""

    async def _run() -> None:
        provider = build_provider("gitea")
        try:
            with session_scope() as session:
                source = get_or_create_source(session, "gitea", settings.gitea_url, "Gitea")
                result = await scan_source(session, provider, source)
        finally:
            await provider.aclose()

        typer.echo("")
        typer.echo(f"  Найдено на хостинге : {result.seen}")
        typer.echo(f"  Пропущено приватных : {result.skipped_private}")
        typer.echo(f"  Добавлено новых     : {result.added}")
        typer.echo(f"  Обновлено           : {result.updated}")
        typer.echo(f"  Пропало             : {result.gone}")
        typer.echo(f"  Всего в базе        : {result.stored}")

    asyncio.run(_run())


@app.command("index")
def index(
    limit: int = typer.Option(None, help="Обработать только первые N репозиториев"),
    force: bool = typer.Option(False, help="Пересобрать, даже если коммит не менялся"),
    no_ai: bool = typer.Option(False, help="Без описаний — только распознать тип"),
) -> None:
    """Собрать карточки: скачать репозитории, распознать, описать."""

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
        typer.echo(f"  Обработано        : {result.processed}")
        typer.echo(f"  Новых карточек    : {result.created}")
        typer.echo(f"  Обновлено         : {result.updated}")
        typer.echo(f"  Без изменений     : {result.unchanged}")
        typer.echo(f"  С описанием       : {result.summarized}")
        typer.echo(f"  Описание не вышло : {result.summary_failed}")
        typer.echo(f"  Ошибок            : {result.failed}")

    asyncio.run(_run())


if __name__ == "__main__":
    app()
