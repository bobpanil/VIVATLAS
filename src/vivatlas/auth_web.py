"""Страницы двери: настройка, вход, второй код, выход.

Отдельно от web.py: те страницы за замком, эти — сам замок. И шаблон у них
свой, без боковой панели с каталогом: пока не вошёл, каталога видеть нельзя.
"""

import ipaddress
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from vivatlas import auth, i18n, mailer, runtime_settings, security, twofactor, usericons
from vivatlas.db import session_scope
from vivatlas.models import User

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
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
            lang = getattr(request.state, "lang", "en")
            return _page(
                request, "setup", error=i18n.translate(err, lang),
                email=email, display_name=display_name,
            )

        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=True,
            avatar_preset=usericons.random_preset(),
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
        can_register = runtime_settings.registration_open(session)
    return _page(request, "login", next=_safe_next(next), can_register=can_register)


@router.post("/login")
def login_do(
    request: Request,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/",
) -> HTMLResponse:
    dest = _safe_next(next)
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        can_register = runtime_settings.registration_open(session)
        result = auth.check_login(session, email, password)

        if result.locked_minutes:
            return _page(
                request,
                "login",
                error=i18n.translate("auth.err.locked", lang, minutes=result.locked_minutes),
                email=email,
                next=dest,
                can_register=can_register,
            )
        if not result.ok:
            return _page(
                request, "login", error=i18n.translate(result.error, lang), email=email,
                next=dest, can_register=can_register,
            )

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
                error=i18n.translate("auth.err.totp_bad", getattr(request.state, "lang", "en")),
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


# --- забыли пароль ---------------------------------------------------------


@router.get("/forgot", response_class=HTMLResponse)
def forgot_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
    return _page(request, "forgot")


async def _send_reset_quietly(cfg, to: str, subject: str, html: str, text: str) -> None:
    """Отправить письмо о сбросе в фоне, проглотив ошибку. В фоне — чтобы ответ
    страницы не зависел от того, есть ли такая почта и удалась ли отправка:
    иначе по времени ответа было бы видно, заведён ли аккаунт."""
    try:
        await mailer.send(cfg, to, subject, html, text)
    except mailer.MailError as exc:
        log.warning("письмо о сбросе не ушло на %s: %s", to, exc)


def _is_local_host(host: str) -> bool:
    """Свой ли это адрес — loopback или домашняя сеть. Только таким доверяем
    подставлять себя в ссылку, когда site_url не задан."""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def _reset_link_base(session, request: Request) -> str | None:
    """Откуда брать домен для ссылки в письме. None — брать неоткуда безопасно.

    Если хозяин задал site_url — берём его. Если нет — можно подставить адрес
    запроса, НО только когда это свой хост (loopback/LAN): за туннелем и вообще
    на публичном адресе Host в запросе задаёт клиент, и доверять ему нельзя —
    иначе ссылку в письме уводят на чужой домен, а по ней меняют пароль
    (reset poisoning). На публичном адресе без site_url ссылку просто не шлём.
    """
    configured = runtime_settings.site_url(session)
    if configured:
        return configured
    host = request.url.hostname or ""
    if _is_local_host(host):
        return str(request.base_url).rstrip("/")
    return None


@router.post("/forgot")
def forgot_do(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Отправить ссылку на смену пароля.

    Ответ всегда один и тот же — «если такая почта есть, письмо ушло». Не
    подтверждаем и не отрицаем, что аккаунт заведён: иначе страница сброса
    становится проверялкой чужих почт. Само письмо уходит фоном.
    """
    email = email.strip().lower()
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        user = session.scalar(select(User).where(User.email == email))
        if user is not None and user.is_active:
            cfg = runtime_settings.get_smtp(session)
            base = _reset_link_base(session, request)
            if not cfg.is_configured:
                log.warning("сброс пароля запрошен, но почта не настроена: %s", email)
            elif base is None:
                log.warning(
                    "сброс пароля: site_url не задан, а адрес запроса (%s) не локальный "
                    "— ссылку не шлём",
                    request.url.hostname,
                )
            else:
                # SecretMissing здесь не роняем: без ключа подписать нельзя, но
                # ответ должен остаться таким же, как для несуществующей почты —
                # иначе 500 против 200 выдаёт, что аккаунт заведён.
                try:
                    token = auth.make_reset_token(user)
                    link = f"{base}/reset?token={token}"
                    html, text = mailer.render(
                        "password_reset",
                        getattr(request.state, "lang", "en"),
                        link=link,
                        name=user.display_name,
                        minutes=auth.RESET_MAX_AGE // 60,
                    )
                except security.SecretMissing:
                    log.error("сброс пароля: нет SECRET_KEY — ссылку не подписать")
                else:
                    background.add_task(
                        _send_reset_quietly, cfg, user.email, "Смена пароля — VivAtlas", html, text
                    )
    return _page(request, "forgot_sent")


# --- смена пароля по ссылке ------------------------------------------------


@router.get("/reset", response_class=HTMLResponse)
def reset_page(request: Request, token: str = "") -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        user = auth.read_reset_token(session, token)
    if user is None:
        return _page(request, "reset_bad")
    return _page(request, "reset", token=token)


@router.post("/reset")
def reset_do(
    request: Request,
    token: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        user = auth.read_reset_token(session, token)
        if user is None:
            return _page(request, "reset_bad")
        if password != password2:
            return _page(
                request, "reset", token=token,
                error=i18n.translate("auth.err.pw_mismatch", lang),
            )
        weak = security.check_password_strength(password)
        if weak:
            return _page(request, "reset", token=token, error=i18n.translate(weak, lang))

        # Новый пароль делает старую ссылку мёртвой (в ней отпечаток прежнего
        # хеша) и рвёт все открытые сессии: если в аккаунт кто-то влез, смена
        # пароля должна его выкинуть, а не оставить сидеть.
        user.password_hash = security.hash_password(password)
        auth.revoke_all(session, user)
    return _page(request, "reset_done")


# --- открытая регистрация (если хозяин её включил) -------------------------


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        if not runtime_settings.registration_open(session):
            return _page(request, "register_closed")
    return _page(request, "register")


@router.post("/register")
def register_do(
    request: Request,
    email: Annotated[str, Form()],
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Самому завести аккаунт — только когда хозяин открыл регистрацию. Новый
    человек всегда обычный (не владелец) и сразу активен: подтверждения почты
    нет, доступ к регистрации и так решает хозяин переключателем."""
    email = email.strip().lower()
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        if not auth.has_any_user(session):
            return RedirectResponse("/setup", status_code=303)
        if not runtime_settings.registration_open(session):
            return _page(request, "register_closed")
        err = _validate(email, password, password2)
        if err:
            return _page(
                request, "register", error=i18n.translate(err, lang),
                email=email, display_name=display_name,
            )
        if session.scalar(select(User).where(User.email == email)) is not None:
            return _page(
                request, "register", error=i18n.translate("auth.err.email_taken", lang),
                email=email, display_name=display_name,
            )
        user = User(
            email=email,
            display_name=display_name.strip() or email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=False,
            avatar_preset=usericons.random_preset(),
        )
        session.add(user)
        session.flush()
        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        return response


# --- приглашение: принять и завести аккаунт --------------------------------


@router.get("/join", response_class=HTMLResponse)
def join_page(request: Request, code: str = "") -> HTMLResponse:
    with session_scope() as session:
        inv = auth.read_invite(session, code)
        if inv is None:
            return _page(request, "join_bad")
        email = inv.email
    return _page(request, "join", code=code, email=email, email_locked=bool(email))


@router.post("/join")
def join_do(
    request: Request,
    code: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    password2: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Принять приглашение: задать имя и пароль, завести аккаунт и войти. Почту
    у привязанного приглашения меняем не даём — она задана хозяином."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        inv = auth.read_invite(session, code)
        if inv is None:
            return _page(request, "join_bad")
        use_email = (inv.email or email).strip().lower()
        locked = bool(inv.email)
        err = _validate(use_email, password, password2)
        if err:
            return _page(
                request, "join", code=code, email=use_email, email_locked=locked,
                error=i18n.translate(err, lang), display_name=display_name,
            )
        if session.scalar(select(User).where(User.email == use_email)) is not None:
            return _page(
                request, "join", code=code, email=use_email, email_locked=locked,
                error=i18n.translate("auth.err.email_taken", lang), display_name=display_name,
            )
        user = User(
            email=use_email,
            display_name=display_name.strip() or use_email.split("@")[0],
            password_hash=security.hash_password(password),
            is_owner=False,
            avatar_preset=usericons.random_preset(),
        )
        session.add(user)
        session.flush()
        # Приглашение — одноразовое: занимаем его атомарно. Не вышло (успели
        # принять параллельно) — откатываем и не заводим второй аккаунт.
        if not auth.consume_invite(session, inv, user):
            session.rollback()
            return _page(request, "join_bad")
        response = RedirectResponse("/", status_code=303)
        auth.open_session(session, user, request, response)
        return response


# --- проверки --------------------------------------------------------------


def _validate(email: str, password: str, password2: str) -> str:
    """Пусто — годится; иначе КЛЮЧ ошибки (переводится на месте показа)."""
    if "@" not in email or len(email) < 5:
        return "auth.err.email_invalid"
    if password != password2:
        return "auth.err.pw_mismatch"
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
