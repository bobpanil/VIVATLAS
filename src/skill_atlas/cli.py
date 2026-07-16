"""Команды в терминале."""

import asyncio
import logging

import typer
from sqlalchemy import select

from skill_atlas import changes as ch
from skill_atlas.ai import build_embedding_model, build_text_model
from skill_atlas.config import settings
from skill_atlas.db import engine, session_scope
from skill_atlas.embeddings import embed_artifact
from skill_atlas.finder import Finder
from skill_atlas.import_run import execute, record_upstream
from skill_atlas.importer import GitHubFetcher, ImportError_, plan_import
from skill_atlas.indexer import index_all, index_repository
from skill_atlas.migrate import ensure_schema, rebuild_fts
from skill_atlas.models import Artifact, Base, Repository, UpstreamLink
from skill_atlas.net import lan_addresses
from skill_atlas.providers import build_provider
from skill_atlas.scanner import get_or_create_source, scan_source
from skill_atlas.search import Mode, index_artifact_for_words
from skill_atlas.search import search as do_search
from skill_atlas.tagger import tag_artifact
from skill_atlas.updater import UpdateRefused, apply_update, plan_update
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


@app.command("find")
def find_cmd(source: str) -> None:
    """Найти репозиторий по чему угодно: ссылке, странице, скриншоту, ролику.

    Ничего не тащит и не создаёт — только показывает, что нашлось. Выбираете
    вы: название на слух и с картинки распознаётся неточно, а ошибка стоит
    дорого. Дальше — обычный import по готовой строчке.

        skill-atlas find https://github.com/DeusData/codebase-memory-mcp
        skill-atlas find https://voltagent.dev/
        skill-atlas find C:/скриншоты/рилс.png
        skill-atlas find "скил который собирает новости за 30 дней"
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
            "github": "ссылка на GitHub",
            "web": "страница в интернете",
            "image": "картинка",
            "video": "ролик",
            "text": "слова",
        }
        typer.echo("")
        typer.echo(f"  Что дали: {kinds.get(result.kind, result.kind)}")
        if result.heard:
            lang = f" ({result.language})" if result.language else ""
            typer.echo(f"  Прочитано{lang}: {result.heard[:150]}")
        if result.gist:
            typer.echo(f"  Это про: {result.gist[:150]}")
        if result.tool_name:
            typer.echo(f"  Название: {result.tool_name}")
        for note in result.notes:
            typer.echo(f"    · {note}")

        if not result.candidates:
            typer.echo("")
            typer.echo("  Ничего не нашлось. Попробуйте дать ссылку прямо.")
            return

        typer.echo("")
        typer.echo(f"  НАШЛОСЬ: {len(result.candidates)}")
        for i, c in enumerate(result.candidates, 1):
            mark = "точно" if c.exact else "похоже"
            typer.echo(f"    {i}. [{mark}] {c.repo} — {c.stars:,} зв.".replace(",", " "))
            if c.description:
                typer.echo(f"       {c.description}")
            typer.echo(f"       {c.why}")

        typer.echo("")
        typer.echo("  Выбрали — тащите:")
        typer.echo(f"    skill-atlas import {result.candidates[0].url}")

    asyncio.run(_run())


@app.command("import")
def import_cmd(
    url: str,
    to: str = typer.Option("skills-lib", help="Организация в Gitea"),
    name: str = typer.Option("", help="Имя у себя (по умолчанию — как у источника)"),
    yes: bool = typer.Option(False, "--yes", help="Выполнить. Без этого только показывает план."),
) -> None:
    """Притащить инструмент по ссылке с GitHub.

    Без --yes только показывает, что будет сделано. Ничего не создаётся.
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
        typer.echo("  ЧТО БУДЕТ СДЕЛАНО")
        typer.echo(
            f"    откуда    : github.com/{plan.source.full_repo}"
            + (f"/{plan.source.path}" if plan.source.path else "")
        )
        typer.echo(f"    создастся : {plan.target_owner}/{plan.target_name}")
        typer.echo(f"    файлов    : {len(plan.files)}, {plan.total_bytes / 1024:.0f} КБ")
        for f in plan.files[:5]:
            typer.echo(f"       {f.path}")
        if len(plan.files) > 5:
            typer.echo(f"       ... ещё {len(plan.files) - 5}")
        for w in plan.warnings:
            typer.echo(f"    ! {w}")

        if not yes:
            typer.echo("")
            typer.echo("  Ничего не сделано. Повторите с --yes, чтобы выполнить.")
            return

        if not settings.gitea_token:
            typer.echo("")
            typer.echo("  Нет GITEA_TOKEN — писать нечем. Впишите токен в .env")
            raise typer.Exit(1)

        provider = build_provider("gitea")
        text_model = build_text_model()
        embed_model = build_embedding_model()
        try:
            with session_scope() as session:
                result = await execute(session, provider, plan, settings.gitea_url)
                session.commit()
                typer.echo("")
                typer.echo(f"  Создано: {result.repo_full_name}, файлов {result.files_written}")

                row = session.get(Repository, result.repository_id)
                await index_repository(session, provider, text_model, row, force=True)
                session.commit()

                art = session.scalar(select(Artifact).where(Artifact.repository_id == row.id))
                record_upstream(session, art.id, plan)
                await embed_artifact(session, embed_model, art)
                await tag_artifact(session, art, text_model)
                index_artifact_for_words(session, art)
                session.commit()

                typer.echo(f"  Карточка: {art.name} [{art.artifact_type}]")
                typer.echo(f"  Описание: {art.summary_short[:70]}")
                typer.echo(f"  Источник записан: {plan.source.full_repo}")
        finally:
            await provider.aclose()
            await text_model.aclose()
            await embed_model.aclose()

    asyncio.run(_run())


@app.command("update")
def update_cmd(
    name: str = typer.Argument("", help="Имя карточки. Пусто — все, где вышла новая версия."),
    yes: bool = typer.Option(False, "--yes", help="Выполнить. Без этого только показывает план."),
) -> None:
    """Поставить новую версию из источника вместо старой.

    Обновляет только то, что вы не трогали. Если копию правили — откажется и
    скажет почему: перезапись затёрла бы вашу правку молча.

    Без --yes только показывает, что будет сделано.
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
                        typer.echo(f"  Карточка «{name}» не найдена или у неё не записан источник.")
                    else:
                        typer.echo("  Обновлять нечего. Сначала: skill-atlas upstream")
                    return

                plans, refused = [], []
                for link in links:
                    try:
                        plans.append(await plan_update(session, provider, checker, link))
                    except UpdateRefused as exc:
                        refused.append((link.artifact.name, str(exc)))
                    except Exception as exc:
                        refused.append((link.artifact.name, f"не проверилось: {exc}"))

                typer.echo("")
                for who, why in refused:
                    typer.echo(f"  — {who}: {why}")

                if not plans:
                    return

                typer.echo("")
                typer.echo("  ЧТО БУДЕТ ЗАМЕНЕНО")
                for p in plans:
                    typer.echo(f"    {p.repo_full_name} · {p.path} ({p.size_kb:.0f} КБ)")
                    typer.echo(f"       откуда: github.com/{p.upstream_repo}/{p.upstream_path}")
                    typer.echo(f"       было {p.old_sha[:8]} -> станет {p.new_sha[:8]}")

                if not yes:
                    typer.echo("")
                    typer.echo("  Ничего не сделано. Повторите с --yes, чтобы выполнить.")
                    return

                if not settings.gitea_token:
                    typer.echo("")
                    typer.echo("  Нет GITEA_TOKEN — писать нечем. Впишите токен в .env")
                    raise typer.Exit(1)

                done = 0
                for p in plans:
                    link = session.get(UpstreamLink, p.link_id)
                    try:
                        await apply_update(session, provider, checker, p)
                        session.commit()
                        done += 1
                        typer.echo(f"  Готово: {p.repo_full_name} · {p.path}")
                    except Exception as exc:
                        session.rollback()
                        typer.echo(f"  ОШИБКА: {p.repo_full_name}: {exc}")
                        continue

                    # Файл сменился — значит описание, теги и поиск устарели.
                    if text_model is None or embed_model is None:
                        typer.echo("     карточку не пересобрал: нет GOOGLE_API_KEY")
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
                        typer.echo(f"     карточка пересобрана: {art.summary_short[:60]}")
                    except Exception as exc:
                        session.rollback()
                        typer.echo(f"     файл обновлён, но карточка не пересобралась: {exc}")

                typer.echo("")
                typer.echo(f"  Обновлено: {done} из {len(plans)}")
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
    days: int = typer.Option(30, help="За сколько дней"),
    stale: bool = typer.Option(False, help="Показать протухшее"),
) -> None:
    """Что появилось, изменилось, пропало и что залежалось."""
    with session_scope() as session:
        if stale:
            items = ch.stale(session)
            oldest, newest = ch.oldest_and_newest(session)
            if not items:
                typer.echo(f"Ничего не залежалось дольше {ch.STALE_DAYS} дней.")
                typer.echo(f"Самому старому: {oldest} дн., самому свежему: {newest} дн.")
                return
            for it in items:
                typer.echo(f"  !  {it.artifact.repository.full_name:44s} {it.reason}")
            return

        events = ch.since(session, days=days)
        if not events:
            typer.echo(f"За {days} дней ничего не происходило.")
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

    # Адреса печатаем здесь, а не в пусковом файле: это окно остаётся открытым,
    # пока сервер работает, а окно пускателя закрывается сразу. Человеку, который
    # запустил двойным щелчком, адрес для телефона виден только тут.
    typer.echo("")
    typer.echo(f"  Skill Atlas, порт {port}")
    typer.echo(f"    на этом компьютере : http://127.0.0.1:{port}")
    if host == "0.0.0.0":
        for ip in lan_addresses():
            typer.echo(f"    с телефона         : http://{ip}:{port}")
        typer.echo("")
        typer.echo("  Телефон должен быть в той же сети.")
    typer.echo("")
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
