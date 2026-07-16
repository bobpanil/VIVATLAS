"""Страницы двери: настройка, вход, второй код, выход.

Отдельно от web.py: те страницы за замком, эти — сам замок. И шаблон у них
свой, без боковой панели с каталогом: пока не вошёл, каталога видеть нельзя.
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from vivatlas import auth, security, twofactor
from vivatlas.db import session_scope
from vivatlas.models import User

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE / "templates"))
router = APIRouter()


def _page(request: Request, step: str, **extra) -> HTMLResponse:
    return templates.TemplateResponse(request, "auth.html", {"step": step, **extra})


def _secure(request: Request) -> bool:
    return request.url.scheme == "https"


# --- первый запуск: завести хозяина ---------------------------------------


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        if auth.has_any_user(session):
            return RedirectResponse("/login", status_code=303)
    return _page(request, "setup")


@router.post("/setup")
def setup_do(
    request: Request,
    email: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    email = email.strip().lower()
    with session_scope() as session:
        # Хозяин заводится один раз. Кто успел первым — тот и хозяин; повторный
        # заход сюда уже ничего не создаёт.
        if auth.has_any_user(session):
            return RedirectResponse("/login", status_code=303)

        err = _validate(email, password, password2)
        if err:
            return _page(request, "setup", error=err, email=email, display_name=display_name)

        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=True,
        )
        session.add(user)
        session.flush()

        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        return response


# --- вход ------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/") -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
    return _page(request, "login", next=_safe_next(next))


@router.post("/login")
def login_do(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse:
    dest = _safe_next(next)
    with session_scope() as session:
        result = auth.check_login(session, email, password)

        if result.locked_minutes:
            return _page(
                request,
                "login",
                error=f"Слишком много попыток. Попробуйте через {result.locked_minutes} мин.",
                email=email,
                next=dest,
            )
        if not result.ok:
            return _page(request, "login", error=result.error, email=email, next=dest)

        if result.needs_totp:
            response = _page(request, "totp", next=dest)
            auth.issue_totp_ticket(response, result.user, _secure(request))
            return response

        response = RedirectResponse(dest, status_code=303)
        auth.open_session(session, result.user, request, response)
        return response


# --- второй код ------------------------------------------------------------


@router.post("/login/2fa")
def login_2fa(
    request: Request,
    code: Annotated[str, Form()] = "",
    use_backup: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse:
    dest = _safe_next(next)
    user_id = auth.read_totp_ticket(request)
    if user_id is None:
        # Билет протух или его нет — начинаем вход заново.
        return RedirectResponse("/login", status_code=303)

    # Пустой код — это не попытка, а переключение вида «код из приложения» /
    # «код восстановления». Показываем нужную форму без ложной ошибки.
    if not code.strip():
        return _page(request, "totp", next=dest, backup=bool(use_backup))

    with session_scope() as session:
        user = session.get(User, user_id)
        if user is None or not user.is_active:
            return RedirectResponse("/login", status_code=303)

        if use_backup:
            good = twofactor.use_backup_code(session, user, code)
        else:
            good = twofactor.verify_totp(user, code)

        if not good:
            return _page(
                request,
                "totp",
                error="Код не подошёл. Проверьте и попробуйте ещё раз.",
                next=dest,
                backup=bool(use_backup),
            )

        response = RedirectResponse(dest, status_code=303)
        auth.open_session(session, user, request, response)
        auth.clear_totp_ticket(response)
        return response


# --- выход -----------------------------------------------------------------


@router.post("/logout")
def logout(request: Request) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    with session_scope() as session:
        auth.close_session(session, request, response)
    return response


# --- проверки --------------------------------------------------------------


def _validate(email: str, password: str, password2: str) -> str:
    if "@" not in email or len(email) < 5:
        return "Впишите настоящую почту — на неё пойдёт сброс пароля."
    if password != password2:
        return "Пароли не совпадают."
    weak = security.check_password_strength(password)
    if weak:
        return weak
    return ""


def _safe_next(target: str) -> str:
    """Куда вернуть после входа. Только внутренний путь: чужой адрес в next —
    это открытая переадресация, ею уводят на поддельную страницу входа."""
    if target.startswith("/") and not target.startswith("//"):
        return target
    return "/"
