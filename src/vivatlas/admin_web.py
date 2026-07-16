"""Панель администратора: то, что касается всей программы, а не одного человека.

Отдельно от обычных настроек: управление пользователями, общими ключами
доступа и AI — дело владельца, а не каждого вошедшего. Всё здесь — только для
владельца; проверка на каждом маршруте.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from vivatlas import auth, security
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.models import User
from vivatlas.web import BASE, _counts

templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()


def _owner_or_403(session, request: Request) -> User:
    me = auth.current_user(session, request)
    if me is None or not me.is_owner:
        raise HTTPException(403, "это раздел владельца")
    return me


def _masked_keys() -> list[dict]:
    """Общие ключи и AI — замаскированно, только для взгляда. Меняются пока в
    .env; редактирование из панели — следующий шаг."""

    def secret(label: str, value: str) -> dict:
        return {"label": label, "value": security.mask_secret(value), "secret": True}

    def plain(label: str, value: str) -> dict:
        return {"label": label, "value": value or "—", "secret": False}

    return [
        plain("Адрес Gitea", settings.gitea_url),
        secret("Токен Gitea", settings.gitea_token),
        secret("Токен GitHub", settings.github_token),
        secret("Ключ Google AI", settings.google_api_key),
        plain("Модель описаний", settings.llm_model),
        plain("Модель поиска", settings.embedding_model),
    ]


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _owner_or_403(session, request)
        users = session.scalars(select(User).order_by(User.created_at)).all()
        rows = [
            {
                "id": u.id,
                "email": u.email,
                "name": u.display_name or "",
                "is_owner": u.is_owner,
                "is_active": u.is_active,
                "is_me": u.id == me.id,
                "last_login": u.last_login_at,
                "totp": bool(u.totp_enabled_at),
            }
            for u in users
        ]
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                "users": rows,
                "keys": _masked_keys(),
                "counts": _counts(session, me.id),
                "nav": "admin",
            },
        )


@router.post("/admin/users/{user_id}/toggle")
def user_toggle(
    request: Request, user_id: int, next: Annotated[str, Form()] = "/admin"
) -> RedirectResponse:
    """Включить или выключить доступ человеку. Себя не выключаем — иначе можно
    запереть самого себя; последнего владельца тоже."""
    with session_scope() as session:
        me = _owner_or_403(session, request)
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(404, "пользователь не найден")
        if target.id == me.id:
            raise HTTPException(400, "нельзя выключить самого себя")
        if target.is_owner and target.is_active:
            other_owner = session.scalar(
                select(User).where(
                    User.is_owner.is_(True),
                    User.is_active.is_(True),
                    User.id != target.id,
                )
            )
            if other_owner is None:
                raise HTTPException(400, "это последний владелец — не выключить")
        target.is_active = not target.is_active
        # Выключили — обрываем открытые сессии, чтобы отказ был сразу, а не по
        # истечении куки.
        if not target.is_active:
            for sess in list(target.sessions):
                session.delete(sess)
    dest = next if next.startswith("/") else "/admin"
    return RedirectResponse(dest, status_code=303)
