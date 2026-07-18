"""Настройки за замком: пока — двухэтапная проверка.

Позже сюда добавятся язык, тема, свои репозитории. Страница одна, разделы
растут.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from sqlalchemy import func, select

from vivatlas import auth, avatars, caticons, catnames, i18n, security, twofactor, usericons
from vivatlas import categories as catperm
from vivatlas import filters as flt
from vivatlas.db import session_scope
from vivatlas.models import Avatar, Category, Source, User
from vivatlas.web import _counts

# Какие хостинги можно подключить своим источником. Работает пока Gitea
# (Codeberg — тот же Forgejo/Gitea). Остальные сохраняются, а обход добавим по
# мере готовности провайдеров.
SOURCE_KINDS = [
    ("gitea", "Gitea"),
    ("github", "GitHub"),
    ("gitlab", "GitLab"),
    ("bitbucket", "Bitbucket"),
    ("codeberg", "Codeberg"),
    ("git", "Другой Git"),
]

log = logging.getLogger(__name__)

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
# У этого модуля свой env шаблонов — глобалы web.py сюда не попадают, поэтому
# иконки категорий регистрируем и здесь.
templates.env.globals["caticon"] = caticons.caticon_svg
router = APIRouter()


def _me(session, request: Request) -> User | None:
    return auth.current_user(session, request)


def _mask_token(enc: str, lang: str = "en") -> str:
    """Замаскированный токен. Если не расшифровался (сменился ключ) — не роняем
    страницу, а честно говорим, что нечитаем."""
    if not enc:
        return ""
    try:
        return security.mask_secret(security.decrypt_secret(enc))
    except Exception:
        return i18n.translate("settings.token_unreadable", lang)


def _my_sources(session, user_id: int, lang: str = "en") -> list[dict]:
    """Свои частные источники. Токен наружу — только замаскированным."""
    rows = session.scalars(
        select(Source).where(Source.owner_user_id == user_id).order_by(Source.created_at)
    ).all()
    kinds = dict(SOURCE_KINDS)
    return [
        {
            "id": s.id,
            "kind_raw": s.kind,
            "kind": kinds.get(s.kind, s.kind),
            "display_name": s.display_name,
            "base_url": s.base_url,
            "has_token": bool(s.token_enc),
            "token_mask": _mask_token(s.token_enc, lang),
        }
        for s in rows
    ]


def _valid_url(u: str) -> bool:
    return u.startswith("http://") or u.startswith("https://")


def _security_page(
    request: Request, session, me, error: str = "", **msgs
) -> HTMLResponse:
    """Полная страница настроек — с ошибкой/сообщением или без. Одна точка сборки
    контекста, чтобы они показывались в окне, а не роняли его. msgs — адресные
    сообщения разделов (account_msg/account_error и т.п.)."""
    lang = getattr(request.state, "lang", "en")
    has_avatar = session.get(Avatar, me.id) is not None
    return _page(
        request,
        session,
        "security",
        me=me,
        totp_on=bool(me.totp_enabled_at),
        backup_left=twofactor.unused_backup_count(me),
        has_avatar=has_avatar,
        avatar_presets=usericons.PRESETS,
        avatar_preset=me.avatar_preset,
        categories=flt.category_options(session, me.id, lang),
        cat_icons=caticons.ICON_SLUGS,
        my_sources=_my_sources(session, me.id, lang),
        source_kinds=SOURCE_KINDS,
        error=error,
        **msgs,
    )


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
        return _security_page(request, session, me)


# --- свой аккаунт: пароль, почта, фото, удаление ---------------------------


def _require_me(session, request: Request) -> User:
    me = _me(session, request)
    if me is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    return me


@router.post("/settings/account/password", response_class=HTMLResponse)
def change_password(
    request: Request,
    current: Annotated[str, Form()] = "",
    new: Annotated[str, Form()] = "",
    confirm: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Сменить свой пароль: сперва подтверждаем текущим, потом проверяем силу."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        if not security.verify_password(current, me.password_hash):
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.bad_current", lang)
            )
        if new != confirm:
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.pw_mismatch", lang)
            )
        key = security.check_password_strength(new)
        if key:
            return _security_page(request, session, me, account_error=i18n.translate(key, lang))
        me.password_hash = security.hash_password(new)
        # Смена пароля выкидывает все сессии (в т.ч. чужую украденную — ради этого
        # пароль и меняют), а текущему человеку сразу выдаём свежую, чтобы его не
        # разлогинило. Так же поступает сброс пароля.
        auth.revoke_all(session, me)
        resp = _security_page(
            request, session, me, account_msg=i18n.translate("account.pw_changed", lang)
        )
        auth.open_session(session, me, request, resp)
        return resp


@router.post("/settings/account/email", response_class=HTMLResponse)
def change_email(
    request: Request,
    email: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Сменить свою почту: подтверждаем паролем, приводим к нижнему регистру,
    бережём уникальность."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        new = email.strip().lower()
        if "@" not in new or len(new) < 5:
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.email_bad", lang)
            )
        if not security.verify_password(password, me.password_hash):
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.bad_current", lang)
            )
        taken = session.scalar(select(User).where(User.email == new, User.id != me.id))
        if taken is not None:
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.email_taken", lang)
            )
        me.email = new
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.email_changed", lang)
        )


@router.post("/settings/account/photo", response_class=HTMLResponse)
def upload_avatar(
    request: Request, photo: Annotated[UploadFile, File()]
) -> HTMLResponse:
    """Загрузить фото профиля. Приводим к квадратному webp (png/jpeg/gif/bmp —
    Pillow; svg — headless-Chromium). СИНХРОННЫЙ маршрут намеренно: растеризация
    svg через sync-Playwright не живёт внутри цикла asyncio."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        # Читаем с потолком в памяти (ещё до Pillow): без ограничителя огромная
        # загрузка целиком осела бы в оперативке до проверки размера.
        data = photo.file.read(avatars.MAX_UPLOAD + 1)
        try:
            webp = avatars.to_webp(data, photo.content_type or "")
        except avatars.AvatarError as exc:
            return _security_page(
                request, session, me, account_error=i18n.translate(str(exc), lang)
            )
        row = session.get(Avatar, me.id)
        if row is None:
            session.add(Avatar(user_id=me.id, webp=webp))
        else:
            row.webp = webp
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.photo_saved", lang)
        )


@router.post("/settings/account/photo/delete", response_class=HTMLResponse)
def delete_avatar(request: Request) -> HTMLResponse:
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        row = session.get(Avatar, me.id)
        if row is not None:
            session.delete(row)
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.photo_removed", lang)
        )


@router.post("/settings/account/avatar-preset", response_class=HTMLResponse)
def set_avatar_preset(
    request: Request, preset: Annotated[str, Form()] = ""
) -> HTMLResponse:
    """Выбрать аватар из набора «бюсты». Загруженное фото берёт верх над набором,
    поэтому при выборе набора удаляем своё фото — иначе выбор не был бы виден."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        if not usericons.is_valid(preset):
            return _security_page(
                request, session, me,
                account_error=i18n.translate("account.avatar_bad", lang),
            )
        me.avatar_preset = preset
        row = session.get(Avatar, me.id)
        if row is not None:
            session.delete(row)
        return _security_page(
            request, session, me, account_msg=i18n.translate("account.avatar_saved", lang)
        )


@router.post("/settings/account/delete", response_class=HTMLResponse)
def delete_account(request: Request, password: Annotated[str, Form()] = "") -> Response:
    """Удалить свой аккаунт. Подтверждаем паролем. Последнего владельца не даём
    удалить (иначе некому управлять). Общие карточки уходят владельцу приложения,
    личное — с человеком (та же чистка, что у админа)."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _require_me(session, request)
        if not security.verify_password(password, me.password_hash):
            return _security_page(
                request, session, me, account_error=i18n.translate("account.err.bad_current", lang)
            )
        if me.is_owner:
            other_owner = session.scalar(
                select(User).where(
                    User.is_owner.is_(True), User.is_active.is_(True), User.id != me.id
                )
            )
            if other_owner is None:
                return _security_page(
                    request, session, me,
                    account_error=i18n.translate("account.err.last_owner", lang),
                )
        # Кому передать общие карточки: любому ДЕЙСТВУЮЩЕМУ владельцу, кроме
        # уходящего — не забаненному, иначе карточки осядут у того, кто не войдёт.
        heir = session.scalar(
            select(User)
            .where(User.is_owner.is_(True), User.is_active.is_(True), User.id != me.id)
            .order_by(User.created_at)
        )
        heir_id = heir.id if heir is not None else me.id
        from vivatlas.admin_web import _purge_user

        response = RedirectResponse("/login", status_code=303)
        auth.close_session(session, request, response)
        _purge_user(session, me, heir_id)
        return response


@router.get("/avatar/{user_id}")
def avatar(request: Request, user_id: int) -> Response:
    """Отдать аватар (webp). За замком: показываем вошедшим (аватар в меню и на
    карточках). Приоритет: загруженное фото → аватар по умолчанию из набора →
    404 (шаблон тогда покажет инициалы). Загруженное фото кэшируем коротко,
    чтобы смена была видна быстро; набор — дольше, он не меняется."""
    with session_scope() as session:
        row = session.get(Avatar, user_id)
        if row is not None:
            return Response(
                content=row.webp,
                media_type="image/webp",
                headers={"Cache-Control": "private, max-age=60"},
            )
        user = session.get(User, user_id)
        if user is not None and user.avatar_preset:
            data = usericons.read_bytes(user.avatar_preset)
            if data is not None:
                return Response(
                    content=data,
                    media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=3600"},
                )
        raise HTTPException(404, "no avatar")


# --- папки-категории: общие (админские) и личные (у каждого свои) -----------
#
# Общие (owner пуст) заводит и ведёт только администратор — это общий каталог.
# Личные (owner задан) заводит и ведёт каждый у себя; чужие личные не видны даже
# администратору. Права на каждом маршруте считает vivatlas.categories.


def _scope_cond(owner_id: int | None):
    """SQL-условие «в той же области владения», чтобы имя/позиция считались в
    пределах общих ИЛИ в пределах личных одного человека."""
    if owner_id is None:
        return Category.owner_user_id.is_(None)
    return Category.owner_user_id == owner_id


def _safe_next(nxt: str) -> str:
    """Куда вернуться после действия над папкой. Общими папками управляют из
    админ-панели (next=/admin), личными — из настроек (по умолчанию). Только
    внутренние адреса, чтобы формой нельзя было увести на чужой сайт."""
    return nxt if nxt.startswith("/") and not nxt.startswith("//") else "/settings"


def _authorize_category(session, request: Request, cat: Category | None):
    """Проверить право вести конкретную папку. Возвращает (me). 404 на чужую
    личную (её существование не подтверждаем), 403 на общую без прав админа."""
    me = _me(session, request)
    if cat is None or not catperm.can_view(cat, me.id):
        raise HTTPException(404, i18n.msg(request, "err.category_not_found"))
    if not catperm.can_manage(cat, me.id, me.is_owner):
        raise HTTPException(403, i18n.msg(request, "err.categories_owner_only"))
    return me


@router.post("/settings/categories", response_class=HTMLResponse)
def category_create(
    request: Request,
    name: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
    scope: Annotated[str, Form()] = "private",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Завести папку. scope=shared — общая (только администратор); иначе личная
    (у каждого своя). Имя уникально в пределах своей области."""
    dest = _safe_next(next)
    with session_scope() as session:
        me = _me(session, request)
        name = name.strip()
        if not name:
            return RedirectResponse(dest, status_code=303)
        if scope == "shared":
            if not me.is_owner:
                raise HTTPException(403, i18n.msg(request, "err.categories_owner_only"))
            owner: int | None = None
        else:
            owner = me.id
        cond = _scope_cond(owner)
        if session.scalar(select(Category).where(Category.name == name, cond)) is None:
            pos = session.scalar(select(func.max(Category.position)).where(cond)) or 0
            # Иконку не выбрали — подберём по смыслу названия; заменить можно потом.
            chosen = icon[:32] or caticons.suggest_icon(name)
            session.add(
                Category(
                    name=name[:128],
                    names_json=catnames.translate_category_name(name),
                    icon=chosen,
                    position=pos + 1,
                    owner_user_id=owner,
                )
            )
    return RedirectResponse(dest, status_code=303)


@router.post("/settings/categories/{cat_id}/update", response_class=HTMLResponse)
def category_update(
    request: Request,
    cat_id: int,
    name: Annotated[str, Form()] = "",
    icon: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Переименовать и/или сменить иконку. Только своей области (личная — своя,
    общая — админ)."""
    with session_scope() as session:
        cat = session.get(Category, cat_id)
        _authorize_category(session, request, cat)
        name = name.strip()
        if name:
            cond = _scope_cond(cat.owner_user_id)
            dup = session.scalar(
                select(Category).where(Category.name == name, Category.id != cat_id, cond)
            )
            if dup is None:
                cat.name = name[:128]
                cat.names_json = catnames.translate_category_name(cat.name)
        cat.icon = icon[:32]
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/settings/categories/{cat_id}/move", response_class=HTMLResponse)
def category_move(
    request: Request,
    cat_id: int,
    dir: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Поменять местами с соседом по порядку (вверх/вниз) — в пределах своей
    области (общие переставляются среди общих, личные — среди своих)."""
    with session_scope() as session:
        cat = session.get(Category, cat_id)
        _authorize_category(session, request, cat)
        cats = session.scalars(
            select(Category)
            .where(_scope_cond(cat.owner_user_id))
            .order_by(Category.position, Category.name)
        ).all()
        # NB: параметр формы `next` затеняет встроенную next(), поэтому индекс
        # ищем без неё.
        matches = [i for i, c in enumerate(cats) if c.id == cat_id]
        idx = matches[0] if matches else None
        if idx is not None:
            swap = idx - 1 if dir == "up" else idx + 1
            if 0 <= swap < len(cats):
                # Переписываем позиции по порядку, поменяв два места, — надёжнее
                # чем менять одно значение (позиции могли совпасть или быть 0).
                cats[idx], cats[swap] = cats[swap], cats[idx]
                for i, c in enumerate(cats):
                    c.position = i
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/settings/categories/reorder", response_class=HTMLResponse)
def category_reorder(
    request: Request,
    order: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/settings",
) -> RedirectResponse:
    """Порядок папок задаётся перетаскиванием: приходит список id по новому
    порядку. Переставляем только те, что человек вправе вести (свои личные или,
    для админа, общие) — чужие в списке молча пропускаем."""
    with session_scope() as session:
        me = _me(session, request)
        for pos, part in enumerate(order.split(",")):
            part = part.strip()
            if part.isdigit():
                cat = session.get(Category, int(part))
                if cat is not None and catperm.can_manage(cat, me.id, me.is_owner):
                    cat.position = pos
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/settings/categories/{cat_id}/delete", response_class=HTMLResponse)
def category_delete(
    request: Request, cat_id: int, next: Annotated[str, Form()] = "/settings"
) -> RedirectResponse:
    with session_scope() as session:
        cat = session.get(Category, cat_id)
        _authorize_category(session, request, cat)
        # Членство в папке (ArtifactCategory) уходит каскадом по ondelete=CASCADE;
        # сами карточки остаются.
        session.delete(cat)
    return RedirectResponse(_safe_next(next), status_code=303)


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
) -> Response:
    with session_scope() as session:
        me = _me(session, request)
        kinds = {k for k, _ in SOURCE_KINDS}
        base_url = base_url.strip()
        # Ошибку показываем В окне, а не роняем его: неверный адрес — обычное
        # дело, из-за него терять всё окно нельзя.
        if kind not in kinds:
            lang = getattr(request.state, "lang", "en")
            return _security_page(
                request, session, me, error=i18n.translate("settings.src.pick_host", lang)
            )
        if not _valid_url(base_url):
            return _security_page(
                request, session, me,
                error=i18n.translate("settings.src.bad_url", getattr(request.state, "lang", "en")),
            )
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


@router.post("/settings/sources/{source_id}/update", response_class=HTMLResponse)
def source_update(
    request: Request,
    source_id: int,
    kind: Annotated[str, Form()] = "",
    display_name: Annotated[str, Form()] = "",
    base_url: Annotated[str, Form()] = "",
    token: Annotated[str, Form()] = "",
) -> Response:
    """Править свой источник: хостинг, название, адрес, токен. Токен меняем
    только если вписан новый — пустое поле оставляет прежний."""
    with session_scope() as session:
        me = _me(session, request)
        src = session.get(Source, source_id)
        if src is None or src.owner_user_id != me.id:
            raise HTTPException(404, i18n.msg(request, "err.source_not_found"))
        kinds = {k for k, _ in SOURCE_KINDS}
        base_url = base_url.strip()
        if base_url and not _valid_url(base_url):
            return _security_page(
                request, session, me,
                error=i18n.translate("settings.src.bad_url", getattr(request.state, "lang", "en")),
            )
        if kind in kinds:
            src.kind = kind
        if base_url:
            src.base_url = base_url[:512]
        src.display_name = (display_name.strip() or src.base_url)[:128]
        if token.strip():
            src.token_enc = security.encrypt_secret(token.strip())
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


@router.post("/settings/sources/{source_id}/scan", response_class=HTMLResponse)
async def source_scan(request: Request, source_id: int) -> Response:
    """Обойти свой источник и собрать карточки в частную зону. Быструю часть
    (список репозиториев) делаем сразу — ошибку доступа/адреса показываем прямо
    в окне. Долгий обход (скачать и описать каждый) уходит в фон, а на главной
    появляется полоса прогресса."""
    from vivatlas.web import launch_user_scan, precheck_user_scan, scan_progress

    user_id = getattr(request.state, "user_id", None)

    # Уже идёт скан — не запускаем второй, просто ведём на главную к полосе.
    prog = scan_progress(user_id)
    if prog and prog.get("state") == "running":
        return RedirectResponse("/", status_code=303)

    # Только мгновенные проверки (без сети): ошибку сразу в окне. Сам обход,
    # включая получение списка репозиториев, уходит в фон — кнопка отвечает
    # немедленно, а прогресс видно полосой на главной.
    error_key, source_name = precheck_user_scan(user_id, source_id)
    if error_key:
        with session_scope() as session:
            me = _me(session, request)
            return _security_page(request, session, me, error=i18n.msg(request, error_key))
    launch_user_scan(user_id, source_id, source_name, getattr(request.state, "lang", "en"))
    return RedirectResponse("/", status_code=303)


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
                error=i18n.msg(request, "settings.2fa.err.bad_code_clock"),
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
                error=i18n.msg(request, "settings.2fa.err.bad_code_no_regen"),
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
                error=i18n.msg(request, "settings.2fa.err.bad_password"),
            )

        me.totp_enabled_at = None
        me.totp_secret_enc = ""
        me.totp_last_code = ""
        for row in list(me.backup_codes):
            me.backup_codes.remove(row)
            session.delete(row)
        return RedirectResponse("/settings", status_code=303)
