"""Admin panel: things that concern the whole program, not a single person.

Kept apart from ordinary settings: managing users, shared access keys, AI, and
email is the owner's job, not every signed-in user's. Everything here is
owner-only; the check runs on every route.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, update

from vivatlas import auth, caticons, i18n, mailer, runtime_settings, security
from vivatlas import filters as flt
from vivatlas.auth_web import _reset_link_base
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.models import Artifact, Source, User
from vivatlas.web import BASE, _counts, _delete_artifact, launch_global_scan

log = logging.getLogger(__name__)

templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
# Shared folders are managed from here — the same folder icon as in settings.
templates.env.globals["caticon"] = caticons.caticon_svg
router = APIRouter()


def _admin_or_403(session, request: Request) -> User:
    """The admin panel is open to the owner and to admins the owner promoted. The few
    owner-only actions (promoting/demoting admins) check is_owner themselves."""
    me = auth.current_user(session, request)
    if me is None or not (me.is_owner or me.is_admin):
        raise HTTPException(403, i18n.msg(request, "err.owner_only_section"))
    return me


def _owner_or_403(session, request: Request) -> User:
    """Stricter: only the owner. Guards the actions that shape who has power —
    promoting or demoting administrators."""
    me = auth.current_user(session, request)
    if me is None or not me.is_owner:
        raise HTTPException(403, i18n.msg(request, "err.owner_only_section"))
    return me


def _config_rows(session, lang: str = "en") -> list[dict]:
    """Operational configuration (addresses, tokens, AI models) for editing from
    the panel. Secrets are masked only. Labels come from the setting-key translation."""
    label = {
        runtime_settings.CFG_GITEA_URL: "admin.key.gitea_url",
        runtime_settings.CFG_GITEA_TOKEN: "admin.key.gitea_token",
        runtime_settings.CFG_GITHUB_USER: "admin.key.github_user",
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
    """Email settings for the page. The password is exposed only as the fact that
    it is "set" plus a mask, never in full."""
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
    """Assemble the full panel page. A single place to build the context, so that
    messages (email saved, test email sent/failed) show up in the modal rather
    than crashing it."""
    users = session.scalars(select(User).order_by(User.created_at)).all()
    rows = [
        {
            "id": u.id,
            "email": u.email,
            "name": u.display_name or "",
            "is_owner": u.is_owner,
            "is_admin": u.is_admin,
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
        # Shared catalogue folders — created and maintained only by the admin, here.
        "categories": flt.category_options(session, me.id, lang),
        "cat_icons": caticons.ICON_SLUGS,
        "nav": "admin",
    }
    ctx.update(extra)
    return templates.TemplateResponse(request, "admin.html", ctx)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        me = _admin_or_403(session, request)
        return _admin_page(request, session, me)


@router.post("/admin/users/{user_id}/toggle")
def user_toggle(
    request: Request, user_id: int, next: Annotated[str, Form()] = "/admin"
) -> RedirectResponse:
    """Enable or disable a person's access. We don't disable ourselves — otherwise
    you could lock yourself out; the last owner either."""
    with session_scope() as session:
        me = _admin_or_403(session, request)
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
        # Disabled — tear down open sessions so the refusal is immediate rather than
        # waiting for the cookie to expire.
        if not target.is_active:
            for sess in list(target.sessions):
                session.delete(sess)
    # Internal path only (not "//..." — otherwise an open redirect to someone else's site).
    dest = next if next.startswith("/") and not next.startswith("//") else "/admin"
    return RedirectResponse(dest, status_code=303)


@router.post("/admin/users/{user_id}/admin")
def user_set_admin(
    request: Request,
    user_id: int,
    make: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/admin",
) -> RedirectResponse:
    """Promote a user to administrator (make="1") or demote one. Owner-only: only the
    owner decides who else has power. The owner is always an admin and is never listed
    as demotable; you can't change your own role here."""
    with session_scope() as session:
        _owner_or_403(session, request)
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(404, i18n.msg(request, "err.user_not_found"))
        if target.is_owner:
            raise HTTPException(400, i18n.msg(request, "err.cant_change_own_role"))
        target.is_admin = make == "1"
    dest = next if next.startswith("/") and not next.startswith("//") else "/admin"
    return RedirectResponse(dest, status_code=303)


@router.get("/admin/ai/models")
async def ai_models(request: Request) -> JSONResponse:
    """The models the saved Google key may use, for the AI settings dropdowns. Reads the
    SAVED key (save it first). On no key or an error we return empty lists plus a reason;
    the page then keeps the manually-typed model as-is."""
    with session_scope() as session:
        _admin_or_403(session, request)
    key = settings.google_api_key
    if not key:
        return JSONResponse({"text": [], "embedding": [], "error": "no-key"})
    from vivatlas.ai.google import list_available_models

    try:
        data = await list_available_models(key, settings.http_timeout_seconds)
    except Exception as exc:  # noqa: BLE001 — any failure just falls back to manual entry
        return JSONResponse({"text": [], "embedding": [], "error": str(exc)[:200]})
    return JSONResponse(data)


# --- configuration (on top of .env) ----------------------------------------


@router.post("/admin/config", response_class=HTMLResponse)
def config_save(
    request: Request,
    gitea_url: Annotated[str | None, Form()] = None,
    gitea_token: Annotated[str | None, Form()] = None,
    github_user: Annotated[str | None, Form()] = None,
    github_token: Annotated[str | None, Form()] = None,
    google_api_key: Annotated[str | None, Form()] = None,
    llm_model: Annotated[str | None, Form()] = None,
    embedding_model: Annotated[str | None, Form()] = None,
) -> HTMLResponse:
    """Save configuration edits. Secrets with an empty field are left untouched (like
    the SMTP password); edits apply to settings immediately — no restart needed.

    The form arrives PARTIAL: "Sources" (Gitea/GitHub) and "AI" (key and models) are
    different tabs and different forms. Fields of the absent tab arrive as None and
    don't reach save_config, otherwise saving one tab would blank out the other."""
    with session_scope() as session:
        me = _admin_or_403(session, request)
        submitted = {
            runtime_settings.CFG_GITEA_URL: gitea_url,
            runtime_settings.CFG_GITEA_TOKEN: gitea_token,
            runtime_settings.CFG_GITHUB_USER: github_user,
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


# --- sources: scan the shared Gitea/GitHub into the common catalogue --------


def _ensure_global_sources(session) -> list[tuple[int, str]]:
    """Materialize the SHARED Gitea/GitHub sources from the saved admin config, so
    the scan machinery has Source rows (owner None => shared cards) to crawl. Keeps
    each source's token in sync with the config. Returns (id, name) per configured host."""
    from vivatlas.providers.github import _account_from as github_account

    out: list[tuple[int, str]] = []

    def upsert(kind: str, base_url: str, name: str, secret: str) -> Source:
        # Match the ONE shared source per kind by (kind, owner is None), not by URL:
        # keying on the URL would strand a row whenever the address changes (e.g. after
        # fixing a mangled GitHub link) and the stale one would keep getting scanned.
        src = session.scalar(
            select(Source).where(Source.kind == kind, Source.owner_user_id.is_(None))
        )
        if src is None:
            src = Source(kind=kind, base_url=base_url, display_name=name)
            session.add(src)
        else:
            src.base_url = base_url
            src.display_name = name
        src.owner_user_id = None
        src.token_enc = security.encrypt_secret(secret) if secret else ""
        session.flush()
        return src

    gitea_url = (settings.gitea_url or "").strip()
    if gitea_url:
        src = upsert("gitea", gitea_url, "Gitea", settings.gitea_token)
        out.append((src.id, src.display_name or "Gitea"))
    # Normalise the account: the admin may paste a full profile URL, and building
    # https://github.com/{that} would double-prefix it.
    account = github_account(settings.github_user)
    if account:
        src = upsert(
            "github", f"https://github.com/{account}", f"GitHub: {account}", settings.github_token
        )
        out.append((src.id, src.display_name or f"GitHub: {account}"))
    return out


@router.post("/admin/scan")
async def admin_scan(request: Request) -> Response:
    """Scan the shared sources now (owner-only). Refreshes the global Source rows
    from the saved config and launches a background crawl; the progress bar shows on
    the home page and the resulting cards are shared with everyone. Reads the SAVED
    config, so the admin should press Save before Scan.

    Must be async: launch_global_scan schedules the crawl with asyncio.create_task,
    which needs the running event loop. A plain `def` endpoint runs in a threadpool
    worker with no loop, and the scan would die with "no running event loop" — the
    same reason its personal-source twin, settings' source_scan, is async."""
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        me = _admin_or_403(session, request)
        sources = _ensure_global_sources(session)
        session.commit()
        if not sources:
            return _admin_page(
                request, session, me,
                config_err=i18n.translate("admin.sources.scan_none", lang),
            )
        me_id = me.id
    launch_global_scan(me_id, [sid for sid, _ in sources], ", ".join(n for _, n in sources), lang)
    return RedirectResponse("/", status_code=303)


# --- email (SMTP) ----------------------------------------------------------


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
    """Save email settings and the site address. An empty password keeps the old one."""
    with session_scope() as session:
        me = _admin_or_403(session, request)
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
    """Send a test email to yourself. We show the error in the modal — it reveals
    what's wrong with the host, port, or login before the first real password-reset
    email even goes out."""
    # We gather the data inside a closed transaction and send outside it (slow).
    with session_scope() as session:
        me = _admin_or_403(session, request)
        cfg = runtime_settings.get_smtp(session)
        site = runtime_settings.site_url(session)
        to = me.email

    if not cfg.is_configured:
        with session_scope() as session:
            me = _admin_or_403(session, request)
            return _admin_page(
                request, session, me,
                smtp_err=i18n.translate(
                    "admin.smtp.fill_first", getattr(request.state, "lang", "en")
                ),
            )

    html, text = mailer.render("test", getattr(request.state, "lang", "en"), site=site)
    try:
        await mailer.send(cfg, to, "Email test — VivAtlas", html, text)
        note = {
            "smtp_msg": i18n.translate(
                "admin.smtp.test_sent", getattr(request.state, "lang", "en"), to=to
            )
        }
    except mailer.MailError as exc:
        note = {"smtp_err": str(exc)}

    with session_scope() as session:
        me = _admin_or_403(session, request)
        return _admin_page(request, session, me, **note)


# --- managing people: registration, invitations, deletion, reset ------------


async def _send_quietly(cfg, to: str, subject: str, html: str, text: str) -> None:
    """Send an email in the background, swallowing mail errors: the page response
    shouldn't depend on whether the email arrived (we also show the link on the page)."""
    try:
        await mailer.send(cfg, to, subject, html, text)
    except mailer.MailError as exc:
        log.warning("email did not go out to %s: %s", to, exc)


def _purge_user(session, target: User, admin_id: int) -> None:
    """Delete a person without breaking the shared catalogue.

    Their SHARED cards are handed to the admin — the catalogue doesn't lose them.
    PRIVATE ones are deleted entirely (notifying those who kept them in favourites).
    Private sources are handed to the admin with the token CLEARED: we don't give away
    someone else's access key, and the source can't be deleted — its repositories have
    no cascade. Private folders, sessions, codes, favourites, and their own invitations
    go away by cascade on deletion.
    """
    # The legacy column private_to_user_id — an FK with CASCADE on users. On migrated
    # rows it still points at a person (new code doesn't write it, but never cleaned it
    # up either). Without nulling it, session.delete(user) would cascade even into the
    # SHARED cards handed to the admin — and on their non-cascade children (embeddings,
    # etc.) the delete would flat-out fail with an error. We break the link on every
    # card that references them.
    session.execute(
        update(Artifact)
        .where(Artifact.private_to_user_id == target.id)
        .values(private_to_user_id=None)
    )
    arts = session.scalars(select(Artifact).where(Artifact.owner_user_id == target.id)).all()
    for art in arts:
        if art.shared:
            art.owner_user_id = admin_id  # shared stays in the catalogue, now under the admin
        else:
            _delete_artifact(session, art, admin_id)  # private — gone entirely, with notifications
    for src in session.scalars(select(Source).where(Source.owner_user_id == target.id)).all():
        src.owner_user_id = admin_id
        src.token_enc = ""
    session.flush()
    session.delete(target)


@router.post("/admin/registration")
def registration_toggle(
    request: Request, enabled: Annotated[str, Form()] = ""
) -> RedirectResponse:
    """Open or close free registration. Checkbox sent — open."""
    with session_scope() as session:
        _admin_or_403(session, request)
        runtime_settings.set_bool(session, runtime_settings.REGISTRATION_OPEN, bool(enabled))
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/invite", response_class=HTMLResponse)
def invite_create(
    request: Request,
    background: BackgroundTasks,
    email: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Create an invitation and show a copyable /join link. If an email is given and
    sending is configured — also by email (to the safe address from site_url)."""
    with session_scope() as session:
        me = _admin_or_403(session, request)
        email = email.strip().lower()
        lang = getattr(request.state, "lang", "en")
        raw = auth.make_invite(session, email, me.id)
        session.flush()
        # The link shown to the admin uses the address of their own request (their own
        # browser sees the real domain). The link in the email uses only the safe base.
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
                    _send_quietly, cfg, email, "Invitation — VivAtlas", html, text
                )
                note["invite_msg"] = i18n.translate("admin.invite.sent", lang, to=email)
        return _admin_page(request, session, me, **note)


@router.post("/admin/users/{user_id}/delete")
def user_delete(request: Request, user_id: int) -> RedirectResponse:
    """Delete a person. Yourself and the last owner — not allowed."""
    with session_scope() as session:
        me = _admin_or_403(session, request)
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
    """Reset a person's password: show a copyable /reset link and send it by email
    if email is configured."""
    with session_scope() as session:
        me = _admin_or_403(session, request)
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
                _send_quietly, cfg, target.email, "Password reset — VivAtlas", html, text
            )
            note["reset_msg"] = i18n.translate("admin.reset.sent", lang, to=target.email)
        return _admin_page(request, session, me, **note)
