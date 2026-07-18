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

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from itsdangerous import (
    BadData,
    BadSignature,
    SignatureExpired,
    TimestampSigner,
    URLSafeTimedSerializer,
)
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from vivatlas import security
from vivatlas.config import settings
from vivatlas.models import Invite, User, UserSession

log = logging.getLogger(__name__)

COOKIE_NAME = "vivatlas_session"
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
        return LoginResult(ok=False, locked_minutes=left, error="auth.err.locked")

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
        return LoginResult(ok=False, error="auth.err.bad_credentials")

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

TOTP_TICKET_COOKIE = "vivatlas_2fa"
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


# --- ссылка на сброс пароля ------------------------------------------------
#
# Ссылка подписана главным ключом, живёт час и не хранится в базе: подделать
# нельзя, а лишней таблицы под одноразовые токены не заводим. «Одноразовость»
# держится на отпечатке пароля: в токен зашит отпечаток текущего хеша, и как
# только пароль сменили (в том числе по этой же ссылке), отпечаток перестаёт
# совпадать — старая ссылка мертва. Так одна ссылка меняет пароль ровно раз.

RESET_MAX_AGE = 3600  # секунд: час на то, чтобы дойти до почты и сменить пароль


def _reset_serializer() -> URLSafeTimedSerializer:
    if not settings.secret_key:
        raise security.SecretMissing("Не задан SECRET_KEY — ссылку сброса не подписать.")
    return URLSafeTimedSerializer(settings.secret_key, salt="skill-atlas/password-reset")


def _pw_fingerprint(password_hash: str) -> str:
    """Короткий отпечаток пароля. Не сам хеш — его в ссылку класть незачем;
    достаточно того, что меняется вместе с паролем и делает ссылку мёртвой."""
    return hashlib.sha256(password_hash.encode("utf-8")).hexdigest()[:16]


def make_reset_token(user: User) -> str:
    """Подписанный токен для ссылки сброса. Зовётся, когда человек попросил."""
    return _reset_serializer().dumps({"uid": user.id, "fp": _pw_fingerprint(user.password_hash)})


def read_reset_token(session: Session, token: str, max_age: int = RESET_MAX_AGE) -> User | None:
    """Кому принадлежит ссылка. None — подделка, протухла или уже сработала.

    Проверяем всё: подпись, срок, что человек есть и активен, и что пароль с
    момента выдачи не менялся (отпечаток). Любая осечка — None, без подсказок.
    """
    if not token:
        return None
    try:
        data = _reset_serializer().loads(token, max_age=max_age)
    except BadData:  # подпись, срок или мусор — BadData покрывает всё это
        return None
    if not isinstance(data, dict):
        return None
    uid = data.get("uid")
    fp = data.get("fp")
    if not isinstance(uid, int) or not isinstance(fp, str):
        return None
    user = session.get(User, uid)
    if user is None or not user.is_active:
        return None
    if not security.same_secret(fp, _pw_fingerprint(user.password_hash)):
        return None
    return user


# --- приглашения ------------------------------------------------------------
#
# Хозяин зовёт человека ссылкой /join?code=… В базе лежит ХЕШ кода, не сам код
# (украдут базу — не получат рабочих ссылок), как и у сессий. Ссылка живёт две
# недели и одноразовая: как приняли, помечаем used_at. Приглашение может быть
# привязано к почте (тогда на /join почта уже задана) или открытым (email="").

INVITE_DAYS = 14


def make_invite(session: Session, email: str, created_by: int) -> str:
    """Завести приглашение и вернуть СЫРОЙ код для ссылки. В базу кладём хеш."""
    raw = security.new_token()
    session.add(
        Invite(
            code_hash=security.token_hash(raw),
            email=(email or "").strip().lower(),
            created_by=created_by,
            expires_at=_now() + timedelta(days=INVITE_DAYS),
        )
    )
    return raw


def read_invite(session: Session, code: str) -> Invite | None:
    """Живое ли приглашение по коду из ссылки. None — подделка, протухло или
    уже принято."""
    if not code:
        return None
    row = session.scalar(select(Invite).where(Invite.code_hash == security.token_hash(code)))
    if row is None or row.used_at is not None or _aware(row.expires_at) <= _now():
        return None
    return row


def consume_invite(session: Session, inv: Invite, user: User) -> bool:
    """Пометить приглашение принятым АТОМАРНО — одноразовость под гонкой.

    Условный UPDATE срабатывает, только пока used_at ещё пусто; rowcount==0 —
    значит его успели принять параллельным запросом (для ОТКРЫТОГО приглашения,
    где у каждого своя почта, уникальность users.email второй аккаунт не поймала
    бы). Тогда заводить аккаунт нельзя — вызывающий откатывает транзакцию."""
    res = session.execute(
        update(Invite)
        .where(Invite.id == inv.id, Invite.used_at.is_(None))
        .values(used_at=_now(), used_by=user.id)
    )
    return res.rowcount == 1


def _client_ip(request: Request) -> str:
    """Адрес посетителя. За туннелем настоящий адрес приходит заголовком."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
