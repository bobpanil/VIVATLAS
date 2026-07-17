"""Настройки за замком: пока — двухэтапная проверка.

Позже сюда добавятся язык, тема, свои репозитории. Страница одна, разделы
растут.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import func, select

from vivatlas import auth, caticons, security, twofactor
from vivatlas import filters as flt
from vivatlas.db import session_scope
from vivatlas.models import Category, Source, User
from vivatlas.web import _counts

# Какие хостинги можно подключить своим источником. Значение — как в scanner.
SOURCE_KINDS = [("github", "GitHub"), ("gitea", "Gitea")]

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
# У этого модуля свой env шаблонов — глобалы web.py сюда не попадают, поэтому
# иконки категорий регистрируем и здесь.
templates.env.globals["caticon"] = caticons.caticon_svg
router = APIRouter()


def _me(session, request: Request) -> User | None:
    return auth.current_user(session, request)


def _my_sources(session, user_id: int) -> list[dict]:
    """Свои частные источники. Токен наружу — только замаскированным."""
    rows = session.scalars(
        select(Source).where(Source.owner_user_id == user_id).order_by(Source.created_at)
    ).all()
    kinds = dict(SOURCE_KINDS)
    return [
        {
            "id": s.id,
            "kind": kinds.get(s.kind, s.kind),
            "display_name": s.display_name,
            "base_url": s.base_url,
            "has_token": bool(s.token_enc),
            "token_mask": security.mask_secret(security.decrypt_secret(s.token_enc))
            if s.token_enc
            else "",
        }
        for s in rows
    ]


def _page(request: Request, session, step: str, **extra) -> HTMLResponse:
    user_id = getattr(request.state, "user_id", None)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"step": step, "counts": _counts(session, user_id), "nav": "settings", **extra},
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        return _page(
            request,
            session,
            "security",
            me=me,
            totp_on=bool(me.totp_enabled_at),
            backup_left=twofactor.unused_backup_count(me),
            categories=flt.category_options(session, me.id),
            cat_icons=caticons.ICON_SLUGS,
            my_sources=_my_sources(session, me.id),
            source_kinds=SOURCE_KINDS,
        )


# --- категории-папки (общие, раскладывает владелец) ------------------------


def _owner_only(session, request: Request) -> None:
    if not _me(session, request).is_owner:
        raise HTTPException(403, "категориями управляет владелец")


@router.post("/settings/categories", response_class=HTMLResponse)
def category_create(
    request: Request,
    name: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
) -> RedirectResponse:
    with session_scope() as session:
        _owner_only(session, request)
        name = name.strip()
        if name and session.scalar(select(Category).where(Category.name == name)) is None:
            pos = session.scalar(select(func.max(Category.position))) or 0
            session.add(Category(name=name[:128], icon=icon[:32], position=pos + 1))
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/categories/{cat_id}/update", response_class=HTMLResponse)
def category_update(
    request: Request,
    cat_id: int,
    name: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
) -> RedirectResponse:
    """Переименовать и/или сменить иконку."""
    with session_scope() as session:
        _owner_only(session, request)
        cat = session.get(Category, cat_id)
        if cat is not None:
            name = name.strip()
            if name and session.scalar(
                select(Category).where(Category.name == name, Category.id != cat_id)
            ) is None:
                cat.name = name[:128]
            cat.icon = icon[:32]
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/categories/{cat_id}/move", response_class=HTMLResponse)
def category_move(
    request: Request, cat_id: int, dir: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """Поменять местами с соседом по порядку (вверх/вниз)."""
    with session_scope() as session:
        _owner_only(session, request)
        cats = session.scalars(
            select(Category).order_by(Category.position, Category.name)
        ).all()
        idx = next((i for i, c in enumerate(cats) if c.id == cat_id), None)
        if idx is not None:
            swap = idx - 1 if dir == "up" else idx + 1
            if 0 <= swap < len(cats):
                # Переписываем позиции по порядку, поменяв два места, — надёжнее
                # чем менять одно значение (позиции могли совпасть или быть 0).
                cats[idx], cats[swap] = cats[swap], cats[idx]
                for i, c in enumerate(cats):
                    c.position = i
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/categories/{cat_id}/delete", response_class=HTMLResponse)
def category_delete(request: Request, cat_id: int) -> RedirectResponse:
    with session_scope() as session:
        _owner_only(session, request)
        cat = session.get(Category, cat_id)
        if cat is not None:
            # Карточки не трогаем — их category_id обнулится по ondelete=SET NULL.
            session.delete(cat)
    return RedirectResponse("/settings", status_code=303)


# --- свои репозитории (частная зона) ---------------------------------------
# У каждого свои источники с токеном. Токен вводит сам человек и только он —
# программа его не набирает и не подставляет; на сервере он ложится
# зашифрованным и наружу выходит лишь замаскированным.


@router.post("/settings/sources", response_class=HTMLResponse)
def source_create(
    request: Request,
    kind: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    base_url: Annotated[str, Form()] = "",
    token: Annotated[str, Form()] = "",
) -> RedirectResponse:
    with session_scope() as session:
        me = _me(session, request)
        kinds = {k for k, _ in SOURCE_KINDS}
        base_url = base_url.strip()
        if kind in kinds and base_url:
            session.add(
                Source(
                    kind=kind,
                    display_name=(display_name.strip() or base_url)[:128],
                    base_url=base_url[:512],
                    owner_user_id=me.id,
                    token_enc=security.encrypt_secret(token.strip()) if token.strip() else "",
                    enabled=True,
                )
            )
    return RedirectResponse("/settings", status_code=303)


@router.post("/settings/sources/{source_id}/delete", response_class=HTMLResponse)
def source_delete(request: Request, source_id: int) -> RedirectResponse:
    with session_scope() as session:
        me = _me(session, request)
        src = session.get(Source, source_id)
        # Удалить можно только свой источник — и только общий трогать нельзя.
        if src is not None and src.owner_user_id == me.id:
            session.delete(src)
    return RedirectResponse("/settings", status_code=303)


# --- включение: показать QR ------------------------------------------------


@router.post("/settings/2fa/start", response_class=HTMLResponse)
def totp_start(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        # Секрет уже сохраняем (зашифрованным), но проверку НЕ включаем: пока
        # человек не введёт код, мы не знаем, что приложение и правда привязано.
        # Включить раньше — запереть себя снаружи от собственного аккаунта.
        secret = twofactor.new_secret()
        me.totp_secret_enc = security.encrypt_secret(secret)
        session.flush()

        uri = twofactor.provisioning_uri(secret, me.email)
        return _page(
            request,
            session,
            "qr",
            qr=Markup(twofactor.qr_svg(uri)),
            secret=secret,  # показываем и вручную: не у всех сканер под рукой
        )


# --- включение: подтвердить кодом -----------------------------------------


@router.post("/settings/2fa/confirm", response_class=HTMLResponse)
def totp_confirm(request: Request, code: Annotated[str, Form()] = "") -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        if not twofactor.verify_totp(me, code):
            secret = security.decrypt_secret(me.totp_secret_enc)
            uri = twofactor.provisioning_uri(secret, me.email)
            return _page(
                request,
                session,
                "qr",
                qr=Markup(twofactor.qr_svg(uri)),
                secret=secret,
                error="Код не подошёл. Проверьте, что часы на телефоне точны.",
            )

        # Код верный — приложение привязано. Включаем и сразу выдаём коды
        # восстановления: без них потеря телефона запирает снаружи навсегда.
        me.totp_enabled_at = datetime.now(UTC)
        codes = twofactor.make_backup_codes(session, me)
        user_id = me.id

        # Делаем запись прочной ПРЯМО СЕЙЧАС, до того как что-то рисуем: если
        # включение не переживёт этот момент, человек не должен увидеть коды,
        # которых в базе нет.
        session.commit()

        # Самопроверка отдельной сессией: правда ли включение легло в базу.
        # Была жалоба, что после сохранения кодов проверка отключалась — эта
        # строчка в журнале скажет точно, дошла запись или откатилась.
        with session_scope() as check:
            stuck = check.get(User, user_id)
            if stuck and stuck.totp_enabled_at is not None:
                log.info("2FA включена и записана: пользователь %s", user_id)
            else:
                log.error("2FA НЕ записалась после подтверждения: пользователь %s", user_id)

        return _page(request, session, "codes", codes=codes, fresh=True)


# --- коды восстановления заново --------------------------------------------


@router.post("/settings/2fa/backup", response_class=HTMLResponse)
def backup_regen(request: Request, code: Annotated[str, Form()] = "") -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if not me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        # Перевыпуск кодов — за кодом из приложения: иначе всякий, кто на минуту
        # сел за открытый экран, распечатает себе новый набор ключей.
        if not twofactor.verify_totp(me, code):
            return _page(
                request,
                session,
                "security",
                me=me,
                totp_on=True,
                backup_left=twofactor.unused_backup_count(me),
                error="Код не подошёл — коды не перевыпущены.",
            )
        codes = twofactor.make_backup_codes(session, me)
        return _page(request, session, "codes", codes=codes, fresh=False)


# --- выключение ------------------------------------------------------------


@router.post("/settings/2fa/disable", response_class=HTMLResponse)
def totp_disable(request: Request, password: Annotated[str, Form()] = "") -> HTMLResponse:
    with session_scope() as session:
        me = _me(session, request)
        if not me.totp_enabled_at:
            return RedirectResponse("/settings", status_code=303)

        # Выключение — за паролем: снимать вторую дверь должен тот, кто знает
        # первую, а не тот, кто просто оказался за чужим экраном.
        if not security.verify_password(password, me.password_hash):
            return _page(
                request,
                session,
                "security",
                me=me,
                totp_on=True,
                backup_left=twofactor.unused_backup_count(me),
                error="Пароль неверный — проверка не выключена.",
            )

        me.totp_enabled_at = None
        me.totp_secret_enc = ""
        me.totp_last_code = ""
        for row in list(me.backup_codes):
            me.backup_codes.remove(row)
            session.delete(row)
        return RedirectResponse("/settings", status_code=303)
