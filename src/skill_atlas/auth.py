"""Вход, сессии, кто сейчас за дверью.

Правила, которые тут держатся:

  - в куке лежит случайный ключ, в базе — его хеш. Украдут базу — не получат
    готовых пропусков, только их отпечатки;
  - кука HttpOnly: скрипт на странице её не прочитает, значит и не утащит;
  - Secure ставится, когда соединение по https. Локально по http кука всё
    равно ходит — иначе войти на самой машине было бы нельзя;
  - при неверном пароле argon2 всё равно считается, даже если такой почты нет.
    Иначе по времени ответа видно, какие почты заведены, а какие нет;
  - перебор запирает аккаунт на время. Считаем неудачи, а не гадаем.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, TimestampSigner
from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_atlas import security
from skill_atlas.config import settings
from skill_atlas.models import User, UserSession

log = logging.getLogger(__name__)

COOKIE_NAME = "skill_atlas_session"
SESSION_DAYS = 30

# Перебор. После стольких неудач подряд аккаунт заперт на столько минут. Мягко:
# цель — измотать перебор, а не наказать хозяина за опечатку.
MAX_FAILS = 8
LOCK_MINUTES = 15

# Хеш несуществующего пароля. Нужен, чтобы для неизвестной почты argon2 работал
# ровно столько же, сколько для известной, — иначе время ответа выдаёт, кто
# заведён. Считается один раз при загрузке модуля.
_DUMMY_HASH = security.hash_password("нет такого пользователя, это заглушка")


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    """Дата из базы — с поясом.

    SQLite часовой пояс не хранит и возвращает дату без него. Сравнить такую
    с datetime.now(UTC) нельзя — Python бросает ошибку. Проверено тестом:
    в бою это роняло бы вход ровно в момент, когда аккаунт заперт. Считаем,
    что в базе всё в UTC — мы только UTC туда и пишем.
    """
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


@dataclass
class LoginResult:
    ok: bool
    user: User | None = None
    needs_totp: bool = False
    locked_minutes: int = 0  # >0 — аккаунт заперт, столько минут осталось
    error: str = ""


def has_any_user(session: Session) -> bool:
    """Есть ли вообще кто-то. Пусто — программу ещё не настроили."""
    return session.scalar(select(User.id).limit(1)) is not None


def check_login(session: Session, email: str, password: str) -> LoginResult:
    """Проверить почту и пароль. Сессию НЕ создаёт — это делает вход отдельно.

    Возвращает, что делать дальше: пустить, спросить второй код или отказать.
    """
    email = email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))

    # Заперт? Проверяем до пароля: перебирать смысла нет, дверь закрыта.
    locked = _aware(user.locked_until) if user else None
    if locked and locked > _now():
        left = int((locked - _now()).total_seconds() // 60) + 1
        return LoginResult(ok=False, locked_minutes=left, error="Слишком много попыток.")

    # Пароль сверяем всегда — и для несуществующей почты тоже, по заглушке.
    stored = user.password_hash if user else _DUMMY_HASH
    ok = security.verify_password(password, stored)

    if not user or not ok or not user.is_active:
        if user:
            user.failed_logins += 1
            if user.failed_logins >= MAX_FAILS:
                user.locked_until = _now() + timedelta(minutes=LOCK_MINUTES)
                user.failed_logins = 0
        # Один и тот же ответ на «нет почты» и «неверный пароль»: не подсказываем
        # перебору, что почта угадана.
        return LoginResult(ok=False, error="Неверная почта или пароль.")

    # Пароль верный — счётчик неудач обнуляем.
    user.failed_logins = 0
    user.locked_until = None

    # Пароль пересчитываем, если настройки argon2 с тех пор ужесточились.
    if security.password_needs_rehash(user.password_hash):
        user.password_hash = security.hash_password(password)

    if user.totp_enabled_at:
        return LoginResult(ok=True, user=user, needs_totp=True)
    return LoginResult(ok=True, user=user)


def open_session(session: Session, user: User, request: Request, response: Response) -> None:
    """Создать сессию и положить куку. Зовётся, когда вход уже подтверждён."""
    raw = security.new_token()
    row = UserSession(
        user_id=user.id,
        token_hash=security.token_hash(raw),
        user_agent=(request.headers.get("user-agent") or "")[:256],
        ip=_client_ip(request),
        expires_at=_now() + timedelta(days=SESSION_DAYS),
    )
    session.add(row)
    user.last_login_at = _now()

    response.set_cookie(
        COOKIE_NAME,
        raw,
        max_age=SESSION_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


def current_user(session: Session, request: Request) -> User | None:
    """Кто сейчас за дверью. None — никто."""
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return None
    row = session.scalar(
        select(UserSession).where(UserSession.token_hash == security.token_hash(raw))
    )
    if row is None or row.revoked_at is not None or _aware(row.expires_at) <= _now():
        return None
    # «Последний раз видели» обновляем не чаще раза в пару минут. Иначе каждый
    # показ любой страницы становился записью в базу, а SQLite пускает одного
    # писателя разом — это лишняя запись на ровном месте и лишний повод для
    # блокировок. Для «где я вошёл» минутная точность и не нужна.
    seen = _aware(row.last_seen_at)
    if seen is None or (_now() - seen) > timedelta(minutes=2):
        row.last_seen_at = _now()
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return user


def close_session(session: Session, request: Request, response: Response) -> None:
    """Выйти: отозвать эту сессию и убрать куку."""
    raw = request.cookies.get(COOKIE_NAME)
    if raw:
        row = session.scalar(
            select(UserSession).where(UserSession.token_hash == security.token_hash(raw))
        )
        if row and row.revoked_at is None:
            row.revoked_at = _now()
    response.delete_cookie(COOKIE_NAME, path="/")


def revoke_all(session: Session, user: User) -> int:
    """Выйти на всех устройствах. Возвращает, сколько сессий закрыто."""
    rows = session.scalars(
        select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
    ).all()
    for row in rows:
        row.revoked_at = _now()
    return len(rows)


# --- билет между паролем и вторым шагом ---------------------------------
#
# Пароль верный, но включена двухэтапная проверка. Нужно донести «этот человек
# прошёл пароль» до страницы второго кода, не открывая ещё сессии. Кладём
# подписанную метку в короткоживущую куку: в базе ничего не храним, а подделать
# нельзя — подпись на главном ключе. Живёт 5 минут: код ввести успеешь, а
# забытый на чужом экране билет протухнет сам.

TOTP_TICKET_COOKIE = "skill_atlas_2fa"
_TICKET_MAX_AGE = 300


def _signer() -> TimestampSigner:
    if not settings.secret_key:
        raise security.SecretMissing("Не задан SECRET_KEY — второй шаг входа не подписать.")
    return TimestampSigner(settings.secret_key, salt="skill-atlas/2fa-ticket")


def issue_totp_ticket(response: Response, user: User, secure: bool) -> None:
    token = _signer().sign(str(user.id)).decode("ascii")
    response.set_cookie(
        TOTP_TICKET_COOKIE,
        token,
        max_age=_TICKET_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def read_totp_ticket(request: Request) -> int | None:
    token = request.cookies.get(TOTP_TICKET_COOKIE)
    if not token:
        return None
    try:
        raw = _signer().unsign(token, max_age=_TICKET_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return int(raw.decode("ascii"))
    except ValueError:
        return None


def clear_totp_ticket(response: Response) -> None:
    response.delete_cookie(TOTP_TICKET_COOKIE, path="/")


def _client_ip(request: Request) -> str:
    """Адрес посетителя. За туннелем настоящий адрес приходит заголовком."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
