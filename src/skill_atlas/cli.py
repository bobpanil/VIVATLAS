"""Команды в терминале."""

import asyncio
import logging

import typer

from skill_atlas.ai import build_text_model
from skill_atlas.config import settings
from skill_atlas.db import engine, session_scope
from skill_atlas.indexer import index_all
from skill_atlas.models import Base
from skill_atlas.providers import build_provider
from skill_atlas.scanner import get_or_create_source, scan_source

app = typer.Typer(help="Skill Atlas")
logging.basicConfig(level=logging.INFO, format="%(message)s")


@app.command("init-db")
def init_db() -> None:
    """Создать таблицы."""
    Base.metadata.create_all(engine)
    typer.echo(f"База готова: {settings.database_url}")


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
