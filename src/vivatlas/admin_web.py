"""Панель администратора: то, что касается всей программы, а не одного человека.

Отдельно от обычных настроек: управление пользователями, общими ключами
доступа, AI и почтой — дело владельца, а не каждого вошедшего. Всё здесь —
только для владельца; проверка на каждом маршруте.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update

from vivatlas import auth, caticons, i18n, mailer, runtime_settings, security
from vivatlas import filters as flt
from vivatlas.auth_web import _reset_link_base
from vivatlas.db import session_scope
from vivatlas.models import Artifact, Source, User
from vivatlas.web import BASE, _counts, _delete_artifact

log = logging.getLogger(__name__)

templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
# Общими папками управляют отсюда — тот же значок папок, что в настройках.
templates.env.globals["caticon"] = caticons.caticon_svg
router = APIRouter()


def _owner_or_403(session, request: Request) -> User:
    me = auth.current_user(session, request)
    if me is None or not me.is_owner:
        raise HTTPException(403, i18n.msg(request, "err.owner_only_section"))
    return me


def _config_rows(session, lang: str = "en") -> list[dict]:
    """Операционная конфигурация (адреса, токены, модели AI) для редактирования
    из панели. Секреты — только маской. Метки — переводом по ключу настройки."""
    label = {
        runtime_settings.CFG_GITEA_URL: "admin.key.gitea_url",
        runtime_settings.CFG_GITEA_TOKEN: "admin.key.gitea_token",
        runtime_settings.CFG_GITHUB_TOKEN: "admin.key.github_token",
        runtime_settings.CFG_GOOGLE_KEY: "admin.key.google_key",
        runtime_settings.CFG_LLM_MODEL: "admin.key.llm_model",
        runtime_settings.CFG_EMBEDDING_MODEL: "admin.key.embedding_model",
    }
    rows = runtime_settings.config_view(session)
    for r in rows:
        r["label"] = i18n.translate(label.get(r["key"], r["key"]), lang)
    return rows


def _smtp_view(session) -> dict:
    """Настройки почты для страницы. Пароль наружу — только фактом «задан» и
    маской, никогда целиком."""
    cfg = runtime_settings.get_smtp(session)
    has_password = bool(runtime_settings.get(session, runtime_settings.SMTP_PASSWORD_ENC, ""))
    return {
        "host": cfg.host,
        "port": cfg.port,
        "security": cfg.security,
        "username": cfg.username,
        "from_addr": cfg.from_addr,
        "from_name": cfg.from_name,
        "has_password": has_password,
        "password_mask": runtime_settings.smtp_password_mask(session) if has_password else "",
        "configured": cfg.is_configured,
        "site_url": runtime_settings.site_url(session),
    }


def _admin_page(request: Request, session, me: User, **extra) -> HTMLResponse:
    """Собрать полную страницу панели. Одна точка сборки контекста, чтобы
    сообщения (сохранили почту, проверочное письмо ушло/не ушло) показывались
    в окне, а не роняли его."""
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
    lang = getattr(request.state, "lang", "en")
    ctx = {
        "users": rows,
        "config": _config_rows(session, lang),
        "smtp": _smtp_view(session),
        "counts": _counts(session, me.id),
        "registration_open": runtime_settings.registration_open(session),
        # Общие папки каталога — заводит и ведёт только администратор, отсюда.
        "categories": flt.category_options(session, me.id, lang),
        "cat_icons": caticons.ICON_SLUGS,
        "nav": "admin",
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _owner_or_403(session, request)
        return _admin_page(request, session, me)


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
            raise HTTPException(404, i18n.msg(request, "err.user_not_found"))
        if target.id == me.id:
            raise HTTPException(400, i18n.msg(request, "err.cant_disable_self"))
        if target.is_owner and target.is_active:
            other_owner = session.scalar(
                select(User).where(
                    User.is_owner.is_(True),
                    User.is_active.is_(True),
                    User.id != target.id,
                )
            )
            if other_owner is None:
                raise HTTPException(400, i18n.msg(request, "err.last_owner"))
        target.is_active = not target.is_active
        # Выключили — обрываем открытые сессии, чтобы отказ был сразу, а не по
        # истечении куки.
        if not target.is_active:
            for sess in list(target.sessions):
                session.delete(sess)
    # Только внутренний путь (не "//..." — иначе открытый редирект на чужой сайт).
    dest = next if next.startswith("/") and not next.startswith("//") else "/admin"
    return RedirectResponse(dest, status_code=303)


# --- конфигурация (поверх .env) --------------------------------------------


@router.post("/admin/config", response_class=HTMLResponse)
def config_save(
    request: Request,
    gitea_url: Annotated[str | None, Form()] = None,
    gitea_token: Annotated[str | None, Form()] = None,
    github_token: Annotated[str | None, Form()] = None,
    google_api_key: Annotated[str | None, Form()] = None,
    llm_model: Annotated[str | None, Form()] = None,
    embedding_model: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Сохранить правки конфигурации. Секреты с пустым полем не трогаем (как
    пароль SMTP); правки сразу накладываются на settings — перезапуск не нужен.

    Форма приходит ЧАСТИЧНОЙ: «Источники» (Gitea/GitHub) и «ИИ» (ключ и модели) —
    разные вкладки и разные формы. Поля отсутствующей вкладки приходят None и в
    save_config не попадают, иначе сохранение одной вкладки обнуляло бы другую."""
    with session_scope() as session:
        me = _owner_or_403(session, request)
        submitted = {
            runtime_settings.CFG_GITEA_URL: gitea_url,
            runtime_settings.CFG_GITEA_TOKEN: gitea_token,
            runtime_settings.CFG_GITHUB_TOKEN: github_token,
            runtime_settings.CFG_GOOGLE_KEY: google_api_key,
            runtime_settings.CFG_LLM_MODEL: llm_model,
            runtime_settings.CFG_EMBEDDING_MODEL: embedding_model,
        }
        runtime_settings.save_config(
            session, {k: v for k, v in submitted.items() if v is not None}
        )
        session.flush()
        lang = getattr(request.state, "lang", "en")
        return _admin_page(
            request, session, me, config_msg=i18n.translate("admin.config.saved", lang)
        )


# --- почта (SMTP) ----------------------------------------------------------


@router.post("/admin/smtp", response_class=HTMLResponse)
def smtp_save(
    request: Request,
    host: Annotated[str, Form()] = "",
    port: Annotated[int, Form()] = 587,
    security_mode: Annotated[str, Form()] = "starttls",
    username: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    from_addr: Annotated[str, Form()] = "",
    from_name: Annotated[str, Form()] = "VivAtlas",
    site_url: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Сохранить настройки почты и адрес сайта. Пустой пароль оставляет прежний."""
    with session_scope() as session:
        me = _owner_or_403(session, request)
        runtime_settings.save_smtp(
            session,
            host=host,
            port=port,
            security_mode=security_mode,
            username=username,
            from_addr=from_addr,
            from_name=from_name,
            password=password or None,
        )
        runtime_settings.set(session, runtime_settings.SITE_URL, site_url.strip().rstrip("/"))
        session.flush()
        lang = getattr(request.state, "lang", "en")
        return _admin_page(request, session, me, smtp_msg=i18n.translate("admin.smtp.saved", lang))


@router.post("/admin/smtp/test", response_class=HTMLResponse)
async def smtp_test(request: Request) -> HTMLResponse:
    """Отправить проверочное письмо себе. Ошибку показываем в окне — по ней
    видно, что не так с узлом, портом или логином, ещё до первого настоящего
    письма о сбросе."""
    # Данные собираем в закрытой транзакции, отправляем — вне её (долго).
    with session_scope() as session:
        me = _owner_or_403(session, request)
        cfg = runtime_settings.get_smtp(session)
        site = runtime_settings.site_url(session)
        to = me.email

    if not cfg.is_configured:
        with session_scope() as session:
            me = _owner_or_403(session, request)
            return _admin_page(
                request, session, me,
                smtp_err=i18n.translate(
                    "admin.smtp.fill_first", getattr(request.state, "lang", "en")
                ),
            )

    html, text = mailer.render("test", getattr(request.state, "lang", "en"), site=site)
    try:
        await mailer.send(cfg, to, "Проверка почты — VivAtlas", html, text)
        note = {
            "smtp_msg": i18n.translate(
                "admin.smtp.test_sent", getattr(request.state, "lang", "en"), to=to
            )
        }
    except mailer.MailError as exc:
        note = {"smtp_err": str(exc)}

    with session_scope() as session:
        me = _owner_or_403(session, request)
        return _admin_page(request, session, me, **note)


# --- управление людьми: регистрация, приглашения, удаление, сброс -----------


async def _send_quietly(cfg, to: str, subject: str, html: str, text: str) -> None:
    """Отправить письмо в фоне, проглотив ошибку почты: ответ страницы не должен
    зависеть от того, дошло ли письмо (ссылку показываем и на странице)."""
    try:
        await mailer.send(cfg, to, subject, html, text)
    except mailer.MailError as exc:
        log.warning("письмо не ушло на %s: %s", to, exc)


def _purge_user(session, target: User, admin_id: int) -> None:
    """Удалить человека, не обрушив общий каталог.

    Его ОБЩИЕ карточки передаём администратору — каталог их не теряет. ЛИЧНЫЕ
    удаляем целиком (с уведомлением тех, кто держал их в избранном). Личные
    источники передаём администратору с ОЧИЩЕННЫМ токеном: чужой ключ доступа не
    отдаём, а удалить источник нельзя — у его репозиториев нет каскада. Личные
    папки, сессии, коды, избранное, свои приглашения уходят каскадом при удалении.
    """
    # Ветхий столбец private_to_user_id — FK с CASCADE на users. У миграционных
    # строк он ещё указывает на человека (новый код его не пишет, но и не чистил).
    # Без обнуления session.delete(user) каскадом снёс бы даже переданные админу
    # ОБЩИЕ карточки — а на их не-каскадных детях (embeddings и пр.) удаление и
    # вовсе упало бы с ошибкой. Рвём связь у всех карточек, что на него ссылаются.
    session.execute(
        update(Artifact)
        .where(Artifact.private_to_user_id == target.id)
        .values(private_to_user_id=None)
    )
    arts = session.scalars(select(Artifact).where(Artifact.owner_user_id == target.id)).all()
    for art in arts:
        if art.shared:
            art.owner_user_id = admin_id  # общее остаётся в каталоге, теперь за админом
        else:
            _delete_artifact(session, art, admin_id)  # личное — совсем, с уведомлениями
    for src in session.scalars(select(Source).where(Source.owner_user_id == target.id)).all():
        src.owner_user_id = admin_id
        src.token_enc = ""
    session.flush()
    session.delete(target)


@router.post("/admin/registration")
def registration_toggle(
    request: Request, enabled: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """Открыть или закрыть свободную регистрацию. Флажок прислан — открыта."""
    with session_scope() as session:
        _owner_or_403(session, request)
        runtime_settings.set_bool(session, runtime_settings.REGISTRATION_OPEN, bool(enabled))
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/invite", response_class=HTMLResponse)
def invite_create(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Завести приглашение и показать копируемую ссылку /join. Если почта задана
    и настроена отправка — ещё и письмом (по безопасному адресу от site_url)."""
    with session_scope() as session:
        me = _owner_or_403(session, request)
        email = email.strip().lower()
        lang = getattr(request.state, "lang", "en")
        raw = auth.make_invite(session, email, me.id)
        session.flush()
        # Ссылка для показа админу — по адресу его же запроса (свой браузер видит
        # настоящий домен). Ссылка в письме — только по безопасной базе.
        show_base = runtime_settings.site_url(session) or str(request.base_url).rstrip("/")
        link = f"{show_base}/join?code={raw}"
        note = {"invite_link": link}
        email_base = _reset_link_base(session, request)
        cfg = runtime_settings.get_smtp(session)
        if email and cfg.is_configured and email_base:
            try:
                html, text = mailer.render(
                    "invite", lang, link=f"{email_base}/join?code={raw}", days=auth.INVITE_DAYS
                )
            except security.SecretMissing:
                pass
            else:
                background.add_task(
                    _send_quietly, cfg, email, "Приглашение — VivAtlas", html, text
                )
                note["invite_msg"] = i18n.translate("admin.invite.sent", lang, to=email)
        return _admin_page(request, session, me, **note)


@router.post("/admin/users/{user_id}/delete")
def user_delete(request: Request, user_id: int) -> RedirectResponse:
    """Удалить человека. Себя и последнего владельца — нельзя."""
    with session_scope() as session:
        me = _owner_or_403(session, request)
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(404, i18n.msg(request, "err.user_not_found"))
        if target.id == me.id:
            raise HTTPException(400, i18n.msg(request, "err.cant_delete_self"))
        if target.is_owner:
            other = session.scalar(
                select(User).where(
                    User.is_owner.is_(True), User.is_active.is_(True), User.id != target.id
                )
            )
            if other is None:
                raise HTTPException(400, i18n.msg(request, "err.last_owner"))
        _purge_user(session, target, me.id)
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/{user_id}/reset", response_class=HTMLResponse)
def user_reset(
    request: Request, background: BackgroundTasks, user_id: int
) -> HTMLResponse:
    """Сбросить пароль человеку: показать копируемую ссылку /reset и отправить её
    письмом, если настроена почта."""
    with session_scope() as session:
        me = _owner_or_403(session, request)
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(404, i18n.msg(request, "err.user_not_found"))
        lang = getattr(request.state, "lang", "en")
        try:
            token = auth.make_reset_token(target)
        except security.SecretMissing:
            return _admin_page(
                request, session, me,
                user_err=i18n.translate("admin.reset.no_secret", lang),
            )
        show_base = runtime_settings.site_url(session) or str(request.base_url).rstrip("/")
        note = {"reset_link": f"{show_base}/reset?token={token}"}
        email_base = _reset_link_base(session, request)
        cfg = runtime_settings.get_smtp(session)
        if cfg.is_configured and email_base:
            html, text = mailer.render(
                "password_reset", lang, link=f"{email_base}/reset?token={token}",
                name=target.display_name, minutes=auth.RESET_MAX_AGE // 60,
            )
            background.add_task(
                _send_quietly, cfg, target.email, "Смена пароля — VivAtlas", html, text
            )
            note["reset_msg"] = i18n.translate("admin.reset.sent", lang, to=target.email)
        return _admin_page(request, session, me, **note)
