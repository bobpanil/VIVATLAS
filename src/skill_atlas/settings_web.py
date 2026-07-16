"""Настройки за замком: пока — двухэтажная проверка.

Позже сюда добавятся язык, тема, свои репозитории. Страница одна, разделы
растут.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from skill_atlas import auth, security, twofactor
from skill_atlas.db import session_scope
from skill_atlas.models import User
from skill_atlas.web import _counts

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()


def _me(session, request: Request) -> User | None:
    return auth.current_user(session, request)


def _page(request: Request, session, step: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"step": step, "counts": _counts(session), "nav": "settings", **extra},
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
        )


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
