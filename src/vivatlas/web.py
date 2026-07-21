"""Pages for humans. The API for programs lives in api.py."""

import asyncio
import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text
from sqlalchemy import update as sa_update

from vivatlas import categories as catperm
from vivatlas import caticons, catnames, i18n, security
from vivatlas import changes as ch
from vivatlas import filters as flt
from vivatlas import purposes as pur
from vivatlas.ai import build_embedding_model, build_text_model
from vivatlas.archive import read_archive
from vivatlas.config import settings
from vivatlas.db import session_scope
from vivatlas.detector import detect
from vivatlas.embeddings import embed_artifact, text_hash, to_blob
from vivatlas.finder import MAX_MEDIA_BYTES, Finder, looks_like_link
from vivatlas.import_run import execute, record_upstream
from vivatlas.importer import GitHubFetcher, ImportError_, plan_import
from vivatlas.indexer import _to_ref, index_repository
from vivatlas.models import (
    Artifact,
    ArtifactCategory,
    ArtifactTag,
    Category,
    Change,
    Embedding,
    Favorite,
    RemovedNotice,
    Repository,
    Source,
    Tag,
    TagSuppression,
    UpstreamLink,
)
from vivatlas.providers import build_provider
from vivatlas.providers.gitea import GiteaProvider
from vivatlas.scanner import get_or_create_source, scan_source
from vivatlas.search import Mode, index_artifact_for_words
from vivatlas.search import search as do_search
from vivatlas.summarizer import summarize
from vivatlas.tagger import ai_tags, apply_tags, derive_tags, tag_artifact
from vivatlas.upstream_sync import discover_for_artifact

BASE = Path(__file__).parent
templates = Jinja2Templates(
    directory=str(BASE / "templates"), context_processors=[i18n.template_context]
)
router = APIRouter()
log = logging.getLogger(__name__)

templates.env.globals["caticon"] = caticons.caticon_svg
# type_name / basis_name / status_name / kind_name — language-dependent labels,
# come from i18n.template_context (context_processors) and are taken from the catalogue.
# Here only the neutral change mark (a symbol, language doesn't matter).
templates.env.globals["kind_mark"] = lambda k: ch.KIND_MARKS.get(k, "·")


def _combine(params: dict, **extra) -> dict:
    """Add something else to the filter set (usually a search query)."""
    out = dict(params)
    out.update({k: v for k, v in extra.items() if v})
    return out


templates.env.filters["combine"] = _combine


def author_of(session, artifact: Artifact) -> str:
    """Who made it.

    The owner in Gitea is our organization (design-lib, skills-lib), not the
    author. The real author is the owner of the source repository. No source —
    the author is unknown, and there's no need to lie about it.
    """
    link = session.scalar(select(UpstreamLink).where(UpstreamLink.artifact_id == artifact.id))
    if link and link.upstream_repo and "/" in link.upstream_repo:
        return link.upstream_repo.split("/")[0]
    return ""


def preview_url(artifact: Artifact) -> str | None:
    """We take the preview straight from Gitea — the repositories are open, no need to proxy."""
    if not artifact.preview_path or not artifact.repository.html_url:
        return None
    branch = artifact.repository.default_branch
    return f"{artifact.repository.html_url}/raw/branch/{branch}/{artifact.preview_path}"


def _counts(session, user_id: int | None = None) -> dict:
    # Count only what's visible to this user: shared plus their own private. Drafts
    # are a separate section, not included in the total count or the types.
    vis = flt.visible_ids(user_id)
    not_draft = Artifact.artifact_type != "draft"
    by_type = session.execute(
        select(Artifact.artifact_type, func.count())
        .where(Artifact.id.in_(vis), not_draft)
        .group_by(Artifact.artifact_type)
        .order_by(func.count().desc())
    ).all()
    mine = 0
    if user_id is not None:
        mine = (
            session.scalar(
                select(func.count())
                .select_from(Artifact)
                .where(Artifact.owner_user_id == user_id, not_draft)
            )
            or 0
        )
    # Favourites — for the badge in the panel: as many as the /?fav=1 view shows
    # (visible, not a draft, for this user). An anonymous user has nothing in favourites.
    favorites = 0
    if user_id is not None:
        favorites = (
            session.scalar(
                select(func.count())
                .select_from(Favorite)
                .join(Artifact, Artifact.id == Favorite.artifact_id)
                .where(Favorite.user_id == user_id, Favorite.artifact_id.in_(vis), not_draft)
            )
            or 0
        )
    return {
        "artifacts": session.scalar(
            select(func.count()).select_from(Artifact).where(Artifact.id.in_(vis), not_draft)
        )
        or 0,
        "mine": mine,
        "favorites": favorites,
        "drafts": flt.draft_count(session, user_id),
        "tags": session.scalar(select(func.count(func.distinct(ArtifactTag.tag_id)))) or 0,
        "by_type": by_type,
    }


def _fav_ids(session, user_id: int | None) -> set[int]:
    """Which cards this user has added to favourites."""
    if user_id is None:
        return set()
    return set(
        session.scalars(select(Favorite.artifact_id).where(Favorite.user_id == user_id))
    )


@router.get("/lang/{code}")
def set_language(request: Request, code: str, next: str = "/") -> RedirectResponse:
    """Switch the interface language: set a cookie and return to where we came from.
    Open without sign-in — the language is changed on the sign-in page too."""
    dest = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(dest, status_code=303)
    i18n.set_lang_cookie(resp, code, secure=request.url.scheme == "https")
    return resp


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    q: str = "",
    type: str = "",
    tag: str = "",
    days: str = "",
    status: str = "",
    owner: str = "",
    fav: str = "",
    cat: str = "",
    purpose: str = "",
    draft: str = "",
    zone: str = "",
    sort: str = "",
) -> HTMLResponse:
    f = flt.Filters(
        type=type, tag=tag, days=days, status=status, owner=owner, fav=fav, cat=cat,
        purpose=purpose, draft=draft, zone=zone, sort=sort,
    )

    # A link pasted into search — searching for it among names is pointless: such
    # text isn't and can't be in the cards. Previously this silently returned
    # nothing. Now we offer what the user actually wanted — to parse it.
    link = looks_like_link(q)

    model = build_embedding_model() if q and not link else None
    try:
        with session_scope() as session:
            user_id = getattr(request.state, "user_id", None)
            lang = getattr(request.state, "lang", "en")
            counts = _counts(session, user_id)
            fav_ids = _fav_ids(session, user_id)
            # The "favourites" counter — only over what's visible: hidden and
            # out-of-zone don't count,
            # otherwise the pill shows more than will actually open.
            fav_visible = (
                session.scalar(
                    select(func.count())
                    .select_from(Artifact)
                    .where(Artifact.id.in_(fav_ids), Artifact.id.in_(flt.visible_ids(user_id)))
                )
                or 0
                if fav_ids
                else 0
            )

            if link:
                items = []
            elif q:
                # Search has already picked by meaning — we apply filters to its results,
                # not to the base: otherwise the order by proximity would be lost. The zone is
                # part of apply, so someone else's private items get filtered out in search too.
                hits = await do_search(session, q, model, mode=Mode.BOTH, limit=200)
                allowed = {
                    a
                    for a in session.scalars(
                        flt.apply(select(Artifact.id), f, fav_ids, user_id, session=session)
                    )
                }
                items = [
                    _card(session, h.artifact, h.reasons, fav_ids, lang, user_id)
                    for h in hits
                    if h.artifact.id in allowed
                ][:60]
            else:
                query = flt.apply(
                    select(Artifact), f, fav_ids, user_id, session=session
                ).order_by(*flt.sort_order(f.sort))
                items = [
                    _card(session, a, [], fav_ids, lang, user_id) for a in session.scalars(query)
                ]

            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "items": items,
                    "q": q,
                    "f": f,
                    "counts": counts,
                    "fav_count": fav_visible,
                    "types": flt.type_options(session, user_id),
                    "categories": flt.category_options(session, user_id, lang),
                    "owners": flt.owner_options(session, user_id),
                    "tag_groups": flt.tag_groups(session, user_id=user_id, lang=lang),
                    "purposes": flt.purpose_options(session, user_id, lang),
                    "periods": flt.period_options(session, user_id, lang),
                    "statuses": flt.status_options(session, user_id, lang),
                    "period_names": {k: i18n.label("period", k, lang) for k in flt.PERIODS},
                    "link": link,
                    "nav": "all",
                    "active_cat": f.cat,
                    "active_draft": bool(f.draft),
                    "scan": scan_progress(user_id),
                    "zone_counts": flt.zone_counts(session, user_id),
                    "removed_notices": _removed_notices(session, user_id),
                },
            )
    finally:
        if model:
            await model.aclose()


@router.post("/favorite/{artifact_id}")
def toggle_favorite(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Add a card to favourites or remove it. Favourites are personal, so
    tied to the signed-in user. Returns JSON for the page, a redirect without a script."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        # Visibility — the same as everywhere: you can favourite only what the
        # user is entitled to see. Otherwise a 200/404 reveals the existence of someone else's
        # private card, and its name later leaks through the removal notice.
        mine = art is not None and art.owner_user_id is not None and art.owner_user_id == user_id
        if art is None or not (art.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        row = session.scalar(
            select(Favorite).where(
                Favorite.user_id == user_id, Favorite.artifact_id == artifact_id
            )
        )
        if row is not None:
            session.delete(row)
            now_fav = False
        else:
            session.add(Favorite(user_id=user_id, artifact_id=artifact_id))
            now_fav = True

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"favorite": now_fav})
    # Without a script: return to where we came from. Internal path only.
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


@router.get("/scan/status")
def scan_status(request: Request) -> JSONResponse:
    """State of the running scan — for the progress bar on the home page. Polled
    by the page every couple of seconds while collection is underway."""
    user_id = getattr(request.state, "user_id", None)
    prog = scan_progress(user_id)
    return JSONResponse(prog or {"state": "idle"})


@router.post("/scan/dismiss")
def scan_dismiss(request: Request) -> JSONResponse:
    """Close the bar (the user clicked ✕ or saw the result)."""
    clear_scan(getattr(request.state, "user_id", None))
    return JSONResponse({"ok": True})


@router.get("/scan/cards")
def scan_cards(request: Request, after: int = 0) -> JSONResponse:
    """Ready-made markup for cards added after `after` (id greater than it).
    While a scan runs, the home page polls this and inserts new cards one by one,
    without reloading the page; along the way we return a fresh total count for the pill."""
    user_id = getattr(request.state, "user_id", None)
    lang = getattr(request.state, "lang", "en")
    with session_scope() as session:
        fav_ids = _fav_ids(session, user_id)
        arts = session.scalars(
            select(Artifact)
            .where(
                Artifact.id.in_(flt.visible_ids(user_id)),
                Artifact.id > after,
                Artifact.artifact_type != "draft",
            )
            .order_by(Artifact.id)
        ).all()
        tmpl = templates.get_template("_carditem.html")
        # request is required: the card computes rights (owner/administrator) from
        # request.state. Without it the template raised UndefinedError on every card.
        html = "".join(
            tmpl.render(
                it=_card(session, a, [], fav_ids, lang, user_id), next_path="/", request=request
            )
            for a in arts
        )
        max_id = arts[-1].id if arts else after
        total = _counts(session, user_id)["artifacts"]
    return JSONResponse({"html": html, "total": total, "max_id": max_id, "count": len(arts)})


_CAT_STOP = {"the", "and", "for", "of"}


def _auto_category(session, art: Artifact, user_id: int | None) -> int | None:
    """Automatically pick a PRIVATE folder for a new tool: for which of this user's
    private folders the words from its name appear in the card's text (name,
    description, purpose, tags). Shared (admin) folders we don't touch — a card is
    put there by hand, and a new card is private for now anyway. If we didn't guess or
    there are no private folders — leave it without a folder, the user will move it by dragging."""
    if user_id is None:
        return None
    cats = session.scalars(
        select(Category).where(Category.owner_user_id == user_id)
    ).all()
    if not cats:
        return None

    purpose = pur.detect_for(session, art.id, art.name)[0].label
    tag_slugs = session.scalars(
        select(Tag.slug).join(ArtifactTag).where(ArtifactTag.artifact_id == art.id)
    )
    text = " ".join(
        [art.name or "", art.summary_short or "", purpose, *(s for s in tag_slugs)]
    ).lower()
    # By whole words, not by substring: otherwise "cli" is found in "clickhouse".
    text_words = set(re.split(r"\W+",text))

    best_id, best_score = None, 0
    for c in cats:
        words = [
            w
            for w in re.split(r"\W+",c.name.lower())
            if len(w) >= 3 and w not in _CAT_STOP
        ]
        score = sum(1 for w in words if w in text_words)
        if score > best_score:
            best_id, best_score = c.id, score
    return best_id


def _zone(a: Artifact) -> str:
    """The card's zone: common if it is shared; otherwise private —
    only the owner sees it."""
    return "common" if a.shared else "private"


def _artifact_categories(session, artifact_id: int, user_id: int | None, lang: str) -> list[dict]:
    """The card's folders that THIS user is entitled to see: shared + their own private.
    Someone else's private membership (someone put a shared card into their own private folder)
    we don't show to others. Shared first, then their own private."""
    rows = session.scalars(
        select(Category)
        .join(ArtifactCategory, ArtifactCategory.category_id == Category.id)
        .where(
            ArtifactCategory.artifact_id == artifact_id,
            Category.id.in_(catperm.visible_category_ids(user_id)),
        )
        .order_by(Category.owner_user_id.is_not(None), Category.position, Category.name)
    ).all()
    return [
        {
            "id": c.id,
            "name": catnames.label(c.names_json, c.name, lang),
            "icon": c.icon,
            "color": caticons.category_color(c.id),
            "owned": c.owner_user_id is not None,
        }
        for c in rows
    ]


def _card(
    session,
    a: Artifact,
    reasons: list[str],
    fav_ids: set[int] = frozenset(),
    lang: str = "en",
    user_id: int | None = None,
) -> dict:
    purpose, _score = pur.detect_for(session, a.id, a.name)
    return {
        "categories": _artifact_categories(session, a.id, user_id, lang),
        "id": a.id,
        "name": a.name,
        "owner": a.repository.owner,
        "type": a.artifact_type,
        "summary_short": a.summary_short,
        "preview_url": preview_url(a),
        # We copy the link to the SOURCE (where it was taken from), not to the store in Gitea.
        # The source is what we show and what people share. No source — let it be
        # the store, better than nothing.
        "source_url": a.repository.original_url or a.repository.html_url,
        "favorite": a.id in fav_ids,
        "zone": _zone(a),
        # Owner and "shared" — so the template decides whether to show the "make shared"
        # / "unshare" / "delete" buttons. The template computes rights from request.state.
        "owner_id": a.owner_user_id,
        "shared": a.shared,
        "is_new": a.is_new,
        "reasons": reasons,
        "author": author_of(session, a),
        "created": a.repository.remote_created_at,
        "updated": a.repository.remote_updated_at,
        "purpose": purpose,
    }


@router.get("/a/{artifact_id}", response_class=HTMLResponse)
def artifact_page(request: Request, artifact_id: int) -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        lang = getattr(request.state, "lang", "en")
        a = session.get(Artifact, artifact_id)
        if a is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        # Zone: someone else's private items we don't show even by direct link. "Not found",
        # not "forbidden" — no need to confirm that such a card exists.
        # Visible if the card is shared or this user is its owner.
        mine = a.owner_user_id is not None and a.owner_user_id == user_id
        if not (a.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))

        # Opened means seen: we clear the "new" badge.
        if a.is_new:
            a.is_new = False

        links = session.scalars(
            select(ArtifactTag).where(ArtifactTag.artifact_id == artifact_id)
        ).all()
        # First manual decisions, then rules, then guesses — by descending
        # reliability, not alphabetically.
        order = {"manual": 0, "derived": 1, "ai": 2}
        tags = sorted(
            (
                {
                    "slug": link.tag.slug,
                    "source": link.source,
                    "confidence": link.confidence,
                    "origin": link.origin,
                }
                for link in links
            ),
            key=lambda t: (order.get(t["source"], 9), -t["confidence"], t["slug"]),
        )
        suppressed = [
            {"slug": s.tag.slug}
            for s in session.scalars(
                select(TagSuppression).where(TagSuppression.artifact_id == artifact_id)
            )
        ]
        upstream = session.scalar(
            select(UpstreamLink).where(UpstreamLink.artifact_id == artifact_id)
        )

        return templates.TemplateResponse(
            request,
            "artifact.html",
            {
                "a": a,
                "tags": tags,
                "suppressed": suppressed,
                "upstream": upstream,
                "author": author_of(session, a),
                "purpose": pur.detect_for(session, a.id, a.name)[0],
                "preview_url": preview_url(a),
                "zone": _zone(a),
                "is_draft": a.artifact_type == "draft",
                "counts": _counts(session, user_id),
                "categories": flt.category_options(session, user_id, lang),
                # Folders the card currently sits in (visible to this user) —
                # for the "Folders" section on the card page: remove/add.
                "art_categories": _artifact_categories(session, a.id, user_id, lang),
                "art_id": a.id,
                # Whether this card can be put into SHARED folders: only a shared one
                # and only by an administrator — shared folders are configured by
                # them. Private ones —
                # always your own.
                "can_file_shared": a.shared and getattr(request.state, "is_admin", False),
                "active_cat": "",
                "active_draft": False,
            },
        )


@router.get("/dev", response_class=HTMLResponse)
def dev_page(request: Request) -> HTMLResponse:
    """A living reference for the design language: colour tokens, fonts, corner radii, buttons,
    fields, icons, cards, menus — on one screen. So the design can be
    "extracted" and kept unified: you edit the set — you check against this."""
    return templates.TemplateResponse(request, "dev.html", {"nav": "dev"})


@router.post("/artifact/{artifact_id}/category")
def set_category(
    request: Request,
    artifact_id: int,
    cat: Annotated[str, Form()] = "",
    op: Annotated[str, Form()] = "add",
    next: Annotated[str, Form()] = "/",
) -> Response:
    """Put a card into a folder (op=add) or take it out (op=remove). Membership is
    many-to-many: one card can be in both a shared folder and private ones.

    Rights: into a SHARED folder it's filed by the card's owner or an administrator, and
    only a shared card; into YOUR OWN PRIVATE folder anyone puts any
    card they're entitled to see; into someone else's private one — not allowed
    (it isn't even visible)."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        # Card visibility: someone else's private items — "not found", so as not to confirm
        # that it exists.
        mine = art is not None and art.owner_user_id is not None and art.owner_user_id == user_id
        if art is None or not (art.shared or mine):
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        category = session.get(Category, int(cat)) if cat.isdigit() else None
        # We don't confirm someone else's private folder's existence — "not found".
        if category is None or not catperm.can_view(category, user_id):
            raise HTTPException(404, i18n.msg(request, "err.category_not_found"))
        is_admin = getattr(request.state, "is_owner", False)
        if not catperm.can_file(art, category, user_id, is_admin):
            raise HTTPException(403, i18n.msg(request, "err.categorize_forbidden"))

        existing = session.scalar(
            select(ArtifactCategory).where(
                ArtifactCategory.artifact_id == art.id,
                ArtifactCategory.category_id == category.id,
            )
        )
        if op == "remove":
            changed = existing is not None
            if existing is not None:
                session.delete(existing)
            member = False
        else:  # add (by default — dragging into a folder)
            changed = existing is None
            if existing is None:
                session.add(ArtifactCategory(artifact_id=art.id, category_id=category.id))
            member = True

    if "application/json" in request.headers.get("accept", ""):
        # changed=false — membership was already like this: the client doesn't move the counters
        # (otherwise a repeat drop onto a folder collapsed into "+N" would double the count).
        return JSONResponse({"ok": True, "cat": int(cat), "member": member, "changed": changed})
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


@router.post("/artifact/{artifact_id}/visibility")
def toggle_visibility(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Toggle the card's zone: private (visible only to the owner) ↔ common
    (everyone sees it).

    ONLY the owner can make something shared — someone else's private items an
    administrator doesn't even
    see, let alone share them on the user's behalf. Unsharing can be done by the owner OR
    an administrator: they're responsible for the shared catalogue. Ownership
    doesn't change in the process —
    a card taken off shared returns to its own owner."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        is_admin = getattr(request.state, "is_owner", False)
        is_owner = art.owner_user_id is not None and art.owner_user_id == user_id
        if art.shared:
            if not (is_owner or is_admin):
                raise HTTPException(403, i18n.msg(request, "err.unshare_owner_or_admin"))
            art.shared = False
            now_private = True
        else:
            if not is_owner:
                raise HTTPException(403, i18n.msg(request, "err.share_owner_only"))
            art.shared = True
            now_private = False

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse(
            {"private": now_private, "zone": "private" if now_private else "common"}
        )
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


def _delete_artifact(session, art: Artifact, actor_user_id: int) -> None:
    """Remove a card from the catalogue entirely — with all its loose ends.

    Whom to warn: a favourite is a link, not a copy, and those who kept the
    card must not have it vanish silently. Plus the owner, if it's deleted
    by someone else (an administrator took down a shared one). We don't notify ourselves.
    """
    aid, name = art.id, art.name

    fav_users = set(
        session.scalars(select(Favorite.user_id).where(Favorite.artifact_id == aid)).all()
    )
    affected = set(fav_users)
    if art.owner_user_id is not None:
        affected.add(art.owner_user_id)
    affected.discard(actor_user_id)
    for uid in affected:
        session.add(RemovedNotice(user_id=uid, artifact_name=name))

    # We delete the related rows ourselves: some foreign keys have no cascade, and without
    # this the database won't let us delete the card. We don't lose the change history — we only
    # detach it from the vanished card.
    session.execute(text("DELETE FROM artifacts_fts WHERE rowid = :id"), {"id": aid})
    session.execute(sa_delete(Embedding).where(Embedding.artifact_id == aid))
    session.execute(sa_delete(ArtifactTag).where(ArtifactTag.artifact_id == aid))
    session.execute(sa_delete(TagSuppression).where(TagSuppression.artifact_id == aid))
    session.execute(sa_delete(UpstreamLink).where(UpstreamLink.artifact_id == aid))
    session.execute(sa_delete(Favorite).where(Favorite.artifact_id == aid))
    session.execute(sa_update(Change).where(Change.artifact_id == aid).values(artifact_id=None))

    # We mark the repository as removed by the user and bury it: otherwise the next scan
    # would see it alive and rebuild the card — "forever" would be a lie.
    from datetime import UTC, datetime

    repo = session.get(Repository, art.repository_id)
    if repo is not None:
        repo.user_removed = True
        repo.gone_at = datetime.now(UTC)

    session.delete(art)


@router.post("/artifact/{artifact_id}/delete")
def delete_artifact(
    request: Request, artifact_id: int, next: Annotated[str, Form()] = "/"
) -> Response:
    """Delete a card. The owner can — any of their own; an administrator — only
    a shared one (they don't peek into someone else's private items, so they
    don't delete them). Those who
    had it in favourites will be left with a "removed" notice."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    is_admin = getattr(request.state, "is_owner", False)
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        is_owner = art.owner_user_id is not None and art.owner_user_id == user_id
        if not (is_owner or (is_admin and art.shared)):
            raise HTTPException(403, i18n.msg(request, "err.delete_owner_or_admin"))
        _delete_artifact(session, art, user_id)

    if "application/json" in request.headers.get("accept", ""):
        return JSONResponse({"ok": True, "deleted": artifact_id})
    dest = next if next.startswith("/") else "/"
    return RedirectResponse(dest, status_code=303)


def _removed_notices(session, user_id: int | None) -> list[dict]:
    """Unread notices "a card was removed from your favourites"."""
    if user_id is None:
        return []
    rows = session.scalars(
        select(RemovedNotice)
        .where(RemovedNotice.user_id == user_id, RemovedNotice.seen_at.is_(None))
        .order_by(RemovedNotice.removed_at.desc())
    ).all()
    return [{"id": r.id, "name": r.artifact_name} for r in rows]


@router.post("/notices/dismiss")
def dismiss_notices(request: Request) -> JSONResponse:
    """Dismiss the notices about removed cards — all at once."""
    user_id = getattr(request.state, "user_id", None)
    if user_id is None:
        raise HTTPException(401, i18n.msg(request, "err.login_required"))
    from datetime import UTC, datetime

    with session_scope() as session:
        session.execute(
            sa_update(RemovedNotice)
            .where(RemovedNotice.user_id == user_id, RemovedNotice.seen_at.is_(None))
            .values(seen_at=datetime.now(UTC))
        )
    return JSONResponse({"ok": True})


# Hosts the Gitea provider can currently scan. Codeberg is
# Forgejo (a Gitea fork), the same API.
_GITEA_KINDS = {"gitea", "codeberg"}


# Progress of running scans: user_id -> {state, total, done, added, source, error}.
# Lives in the memory of a single server process (8710). A scan is a background task of the same
# loop; the home page polls the status and draws the bar. It's lost on
# restart — that's fine: the bar just disappears, the cards remain.
_SCANS: dict[int, dict] = {}
# We keep a strong reference to the background tasks: create_task holds only a weak one, and
# without this the garbage collector may kill a scan mid-way.
_SCAN_TASKS: set = set()


def scan_progress(user_id: int | None) -> dict | None:
    """The scan state of this user for the progress bar. None if there is none."""
    if user_id is None:
        return None
    return _SCANS.get(user_id)


def clear_scan(user_id: int | None) -> None:
    """Remove the bar after the user has closed it or seen the result."""
    if user_id is not None:
        _SCANS.pop(user_id, None)


def precheck_user_scan(user_id: int | None, source_id: int) -> tuple[str, str]:
    """Instant checks without the network: it's your own source, the host is supported, the token
    is present and readable. Returns (error_key, source_name) — the error is
    translated by the caller in the request's language. The network (the
    repository list) and everything
    else — already in the background, so the button responds immediately."""
    with session_scope() as session:
        src = session.get(Source, source_id)
        if src is None or src.owner_user_id != user_id:
            return ("scan.err.source_not_found", "")
        kind, token_enc, name = src.kind, src.token_enc, src.display_name

    if kind not in _GITEA_KINDS:
        return ("scan.err.gitea_only", "")
    if not token_enc:
        return ("scan.err.no_token", "")
    try:
        security.decrypt_secret(token_enc)
    except Exception:
        return ("scan.err.token_unreadable", "")
    return ("", name)


def _provider_for(kind: str, base_url: str, token: str):
    """Build the provider for a source. base_url holds the Gitea host or, for
    GitHub, the account's profile URL (the account is parsed out of it)."""
    if kind == "github":
        from vivatlas.providers.github import GitHubProvider

        return GitHubProvider(user=base_url, token=token, timeout=settings.http_timeout_seconds)
    return GiteaProvider(base_url=base_url, token=token, timeout=settings.http_timeout_seconds)


def launch_user_scan(user_id: int, source_id: int, source_name: str, lang: str = "en") -> None:
    """Start a background scan of one personal source. Sets up the progress bar and
    hands back control immediately — the whole crawl runs as a task of the same loop,
    and the button instantly leads to the home page where the bar is visible."""
    _SCANS[user_id] = {
        "state": "running", "total": 0, "done": 0, "added": 0, "source": source_name, "error": "",
    }
    # Manual launch — a full rebuild (force): the user clicked and is waiting for something fresh.
    task = asyncio.create_task(_scan_task([source_id], _SCANS[user_id], force=True, lang=lang))
    _SCAN_TASKS.add(task)
    task.add_done_callback(_SCAN_TASKS.discard)


def launch_global_scan(
    admin_user_id: int, source_ids: list[int], source_name: str, lang: str = "en"
) -> None:
    """Admin-triggered scan of the SHARED sources (Gitea/GitHub). Progress is keyed
    by the admin who started it, so the bar shows on their home page; the cards are
    shared (the sources have no owner) and appear in everyone's catalogue."""
    _SCANS[admin_user_id] = {
        "state": "running", "total": 0, "done": 0, "added": 0, "source": source_name, "error": "",
    }
    task = asyncio.create_task(_scan_task(source_ids, _SCANS[admin_user_id], force=True, lang=lang))
    _SCAN_TASKS.add(task)
    task.add_done_callback(_SCAN_TASKS.discard)


async def _scan_task(
    source_ids: list[int], progress: dict, force: bool, lang: str
) -> None:
    """Run several sources under one progress bar, then set the final state."""
    try:
        for sid in source_ids:
            await _scan_one_source(sid, progress, force=force, lang=lang)
        progress.update(state="done")
    except Exception as exc:
        log.exception("scan task failed")
        progress.update(state="error", error=str(exc))


async def _scan_one_source(
    source_id: int,
    progress: dict | None = None,
    force: bool = False,
    lang: str = "en",
) -> None:
    """A full background crawl of one source: fetch the repository list, then one by
    one — download, describe, file into the source's zone. A SHARED source (no owner)
    yields shared cards visible to everyone; a PERSONAL source yields cards private to
    its owner — index_repository sets owner/zone from the source, so we don't override
    it here. With `progress` (manual launch) the home-page bar moves; without it (the
    daily auto-scan) we work quietly. `force` rebuilds even unchanged repos. A failure
    on one repository doesn't take down the rest; a fatal failure is raised to the caller."""

    def bump(key: str, n: int = 1) -> None:
        if progress is not None:
            progress[key] = progress.get(key, 0) + n

    with session_scope() as session:
        src = session.get(Source, source_id)
        if src is None:
            return
        base_url, token_enc, kind, owner_uid = (
            src.base_url, src.token_enc, src.kind, src.owner_user_id
        )
    # Personal sources may pull the owner's private repos; a shared source never does.
    include_private = owner_uid is not None
    try:
        token = security.decrypt_secret(token_enc) if token_enc else ""
    except Exception as exc:
        raise RuntimeError(i18n.translate("scan.err.token_lost", lang)) from exc

    provider = _provider_for(kind, base_url, token)
    text_model = build_text_model()
    embed_model = build_embedding_model()
    try:
        # Fetch and save the repository list. While total is unchanged, the bar
        # honestly says "reading the repository list…".
        with session_scope() as session:
            src = session.get(Source, source_id)
            await scan_source(session, provider, src, include_private=include_private)
            session.commit()
            repo_ids = list(
                session.scalars(
                    select(Repository.id).where(
                        Repository.source_id == source_id, Repository.gone_at.is_(None)
                    )
                )
            )
        bump("total", len(repo_ids))
        # Building a card is almost all waiting — on the archive download and on three
        # AI calls (describe, embed, tag). Doing them one at a time wastes that idle
        # time, so we build several at once. The catch: SQLite holds a write lock for
        # the whole of a write transaction, so we must NOT keep one open across the AI
        # calls, or the workers would just queue on the lock and time out. Hence two
        # phases — a parallel COMPUTE (network/CPU/AI, no DB lock held) and a short,
        # serialized PERSIST that writes the finished card in one quick burst.
        sem = asyncio.Semaphore(max(1, settings.scan_concurrency))
        persist_lock = asyncio.Lock()

        async def _handle(rid: int) -> None:
            try:
                async with sem:
                    plan = await _compute_card(rid, provider, text_model, embed_model, force)
                # "unchanged" (same commit, card already built) — skip the write entirely.
                if plan is not None and not plan.unchanged:
                    async with persist_lock:
                        created = _persist_card(plan, owner_uid)
                    if created:
                        bump("added")
            except Exception:
                log.exception("scan: repository %s failed to build", rid)
            finally:
                bump("done")

        await asyncio.gather(*(_handle(rid) for rid in repo_ids))
    finally:
        await provider.aclose()
        await text_model.aclose()
        await embed_model.aclose()


@dataclass
class _CardPlan:
    """Everything needed to write one card, computed off the DB write path so the
    persist step is a quick, lock-free-until-the-last-moment burst."""

    row_id: int
    unchanged: bool
    head: str
    contents: object | None = None
    detection: object | None = None
    content_hash: str = ""
    summaries: dict | None = None
    summary_error: str | None = None
    summary_model: str | None = None
    tag_origin: str = "model"
    embed_vector: list[float] | None = None
    embed_hash: str = ""
    embed_model_name: str = "unknown"
    embed_dim: int = 0
    ai_tag_candidates: list | None = None


async def _compute_card(
    rid: int, provider, text_model, embed_model, force: bool
) -> _CardPlan | None:
    """The parallel half of a scan: download the archive, detect the type, and make
    the three AI calls (describe, embed, tag). Holds no write transaction, so many of
    these run at once. Returns a plan for _persist_card, or a cheap "unchanged" marker
    when the commit and the summary are already in place (the daily auto-scan case)."""
    with session_scope() as session:
        row = session.get(Repository, rid)
        if row is None:
            return None
        ref = _to_ref(row)
        name = row.name
        art = session.scalar(select(Artifact).where(Artifact.repository_id == rid))
        existing_commit = art.source_commit if art is not None else None
        existing_has_summary = bool(art is not None and art.summary_short)

    head = await provider.get_head_sha(ref)
    if existing_commit == head and existing_has_summary and not force:
        return _CardPlan(row_id=rid, unchanged=True, head=head)

    blob = await provider.download_archive(ref, head)
    # gzip/tar parsing, type detection and hashing are pure CPU — off the event loop
    # so a worker doing them doesn't stall the others' downloads and AI calls.
    contents = await asyncio.to_thread(read_archive, blob)
    detection = await asyncio.to_thread(detect, contents)
    content_hash = await asyncio.to_thread(lambda: hashlib.sha256(blob).hexdigest())

    summaries = {"summary_short": "", "summary_normal": "", "summary_technical": ""}
    summary_error: str | None = None
    summary_model: str | None = None
    if text_model is not None:
        try:
            summaries = await summarize(
                text_model,
                full_name=ref.full_name,
                artifact_type=detection.artifact_type,
                doc_text=detection.doc_text,
                file_count=len(contents.files),
            )
            summary_model = getattr(text_model, "model", None)
        except Exception as exc:  # noqa: BLE001 — the card is kept, minus its summary
            summary_error = str(exc)[:500]
            log.warning("scan: %s summary failed: %s", ref.full_name, exc)

    # The embedding text mirrors embeddings.embedding_text — name plus the summaries,
    # falling back to the name alone when there is no summary yet.
    parts = [
        name,
        detection.artifact_type,
        summaries["summary_short"],
        summaries["summary_normal"],
        summaries["summary_technical"],
    ]
    embed_text = "\n".join(p for p in parts if p) or name
    vector = await embed_model.embed(embed_text)

    shim = SimpleNamespace(
        name=name,
        artifact_type=detection.artifact_type,
        summary_normal=summaries["summary_normal"],
        summary_technical=summaries["summary_technical"],
    )
    tag_candidates = await ai_tags(text_model, shim) if text_model is not None else []

    return _CardPlan(
        row_id=rid,
        unchanged=False,
        head=head,
        contents=contents,
        detection=detection,
        content_hash=content_hash,
        summaries=summaries,
        summary_error=summary_error,
        summary_model=summary_model,
        tag_origin=getattr(text_model, "model", "model"),
        embed_vector=vector,
        embed_hash=text_hash(embed_text),
        embed_model_name=getattr(embed_model, "model", "unknown"),
        embed_dim=embed_model.dim,
        ai_tag_candidates=tag_candidates,
    )


def _persist_card(plan: _CardPlan, owner_uid: int | None) -> bool:
    """The serialized half of a scan: write one finished card in a single short
    transaction. Synchronous by design — nothing awaits inside, so only one runs at a
    time and SQLite never sees competing writers. Returns True when a card is created.
    Mirrors indexer.index_repository plus the embed/tag/file-in steps its caller did."""
    with session_scope() as session:
        row = session.get(Repository, plan.row_id)
        if row is None:
            return False
        detection = plan.detection
        artifact = session.scalar(select(Artifact).where(Artifact.repository_id == row.id))
        if artifact is None:
            # Ownership and "shared" follow the source owner, exactly as index_repository
            # does it: a shared source (no owner) yields shared cards, a personal one
            # yields private cards tied to the owner.
            src_owner = row.source.owner_user_id
            artifact = Artifact(
                repository_id=row.id, owner_user_id=src_owner, shared=src_owner is None
            )
            session.add(artifact)
            created = True
        else:
            created = False

        content_changed = artifact.content_hash not in (None, plan.content_hash)
        previous_type = artifact.artifact_type

        artifact.name = row.name
        artifact.artifact_type = detection.artifact_type
        artifact.confidence = detection.confidence
        artifact.detect_reasons = "; ".join(detection.reasons)
        artifact.anchor_path = detection.anchor_path
        artifact.preview_path = detection.preview_path
        artifact.doc_text = detection.doc_text
        artifact.file_count = len(plan.contents.files)
        artifact.file_paths = json.dumps(plan.contents.paths, ensure_ascii=False)
        artifact.source_commit = plan.head
        artifact.content_hash = plan.content_hash
        artifact.updated_at = datetime.now(UTC)
        if plan.summary_error is None:
            artifact.summary_short = plan.summaries["summary_short"]
            artifact.summary_normal = plan.summaries["summary_normal"]
            artifact.summary_technical = plan.summaries["summary_technical"]
            artifact.summary_model = plan.summary_model
            artifact.summary_error = None
        else:
            artifact.summary_error = plan.summary_error

        row.last_scanned_commit = plan.head
        row.last_scanned_at = datetime.now(UTC)

        session.flush()  # artifact.id is needed below
        discover_for_artifact(
            session, artifact, plan.contents, original_url=row.original_url or ""
        )

        if created:
            ch.record(
                session,
                "added",
                repository_id=row.id,
                artifact_id=artifact.id,
                title=row.full_name,
                details=f"type: {detection.artifact_type}",
            )
        elif content_changed:
            what = "content updated"
            if previous_type and previous_type != detection.artifact_type:
                what += f"; type changed: {previous_type} -> {detection.artifact_type}"
            ch.record(
                session,
                "updated",
                repository_id=row.id,
                artifact_id=artifact.id,
                title=row.full_name,
                details=what,
            )

        # Embedding: upsert, same shape as embeddings.embed_artifact's write half.
        if plan.embed_vector is not None:
            emb = session.scalar(
                select(Embedding).where(
                    Embedding.artifact_id == artifact.id,
                    Embedding.model == plan.embed_model_name,
                )
            )
            if emb is None:
                session.add(
                    Embedding(
                        artifact_id=artifact.id,
                        model=plan.embed_model_name,
                        dim=plan.embed_dim,
                        vector=to_blob(plan.embed_vector),
                        source_hash=plan.embed_hash,
                    )
                )
            else:
                emb.vector = to_blob(plan.embed_vector)
                emb.dim = plan.embed_dim
                emb.source_hash = plan.embed_hash

        # Tags: rule-derived first, then the model's — order matters (see tagger).
        apply_tags(session, artifact, derive_tags(artifact), source="derived", origin="rule")
        if plan.ai_tag_candidates:
            apply_tags(
                session, artifact, plan.ai_tag_candidates, source="ai", origin=plan.tag_origin
            )
        index_artifact_for_words(session, artifact)

        if created:
            artifact.hidden = False
            artifact.is_new = True
            # A personal card is filed into the owner's guessed folder; a shared card is
            # left folderless for the admin to place.
            if owner_uid is not None:
                auto_cid = _auto_category(session, artifact, owner_uid)
                if auto_cid is not None:
                    session.add(
                        ArtifactCategory(artifact_id=artifact.id, category_id=auto_cid)
                    )
        session.commit()
    return created


async def retry_failed_summaries(limit: int = 25) -> int:
    """Re-attempt the AI summary for cards that don't have one — a prior Gemini failure
    (usually a rate limit). Uses the documentation ALREADY stored on the card, so there
    is no re-download; working cards (those with a summary) are left untouched. Returns
    how many were fixed. Called periodically so a temporary quota hiccup heals itself
    without waiting for the daily crawl."""
    try:
        text_model = build_text_model()
    except Exception:
        return 0  # no AI key configured — nothing to retry with
    try:
        embed_model = build_embedding_model()
    except Exception:
        embed_model = None
    fixed = 0
    try:
        with session_scope() as session:
            ids = list(
                session.scalars(
                    select(Artifact.id)
                    .where(
                        Artifact.artifact_type != "draft",
                        (Artifact.summary_short == "") | (Artifact.summary_short.is_(None)),
                    )
                    .limit(limit)
                )
            )
        for aid in ids:
            try:
                with session_scope() as session:
                    art = session.get(Artifact, aid)
                    if art is None or art.summary_short or art.repository is None:
                        continue
                    summaries = await summarize(
                        text_model,
                        full_name=art.repository.full_name,
                        artifact_type=art.artifact_type,
                        doc_text=art.doc_text or "",
                        file_count=art.file_count or 0,
                    )
                    art.summary_short = summaries["summary_short"]
                    art.summary_normal = summaries["summary_normal"]
                    art.summary_technical = summaries["summary_technical"]
                    art.summary_model = getattr(text_model, "model", None)
                    art.summary_error = None
                    if embed_model is not None:
                        await embed_artifact(session, embed_model, art)
                    await tag_artifact(session, art, text_model)
                    index_artifact_for_words(session, art)
                    session.commit()
                    fixed += 1
            except Exception:
                # Still failing (quota again?) — leave it for the next pass.
                log.warning("retry: card %s still without a summary", aid)
    finally:
        await text_model.aclose()
        if embed_model is not None:
            await embed_model.aclose()
    return fixed


async def rescan_artifact(artifact_id: int) -> bool:
    """Rebuild ONE card from its source, forced: re-download, re-detect, and (if an AI
    key is set) re-summarize, re-embed and re-tag. For a card's owner or an admin who
    wants a fresh pass now instead of waiting for the daily crawl. Missing AI key isn't
    fatal — detection and rule tags still refresh. Returns False if the card is gone."""
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None or art.repository is None:
            return False
        repo_id = art.repository_id
        src = art.repository.source
        kind, base_url, token_enc = src.kind, src.base_url, src.token_enc
    try:
        token = security.decrypt_secret(token_enc) if token_enc else ""
    except Exception:
        token = ""
    provider = _provider_for(kind, base_url, token)
    try:
        text_model = build_text_model()
    except Exception:
        text_model = None  # no AI key — refresh detection and rule tags anyway
    try:
        embed_model = build_embedding_model()
    except Exception:
        embed_model = None
    try:
        with session_scope() as session:
            repo = session.get(Repository, repo_id)
            if repo is None:
                return False
            await index_repository(session, provider, text_model, repo, force=True)
            art = session.scalar(select(Artifact).where(Artifact.repository_id == repo_id))
            if art is not None:
                if embed_model is not None:
                    await embed_artifact(session, embed_model, art)
                await tag_artifact(session, art, text_model)
                index_artifact_for_words(session, art)
            session.commit()
    finally:
        await provider.aclose()
        if text_model is not None:
            await text_model.aclose()
        if embed_model is not None:
            await embed_model.aclose()
    return True


@router.post("/artifact/{artifact_id}/edit")
async def edit_artifact(
    request: Request,
    artifact_id: int,
    name: Annotated[str, Form()] = "",
    artifact_type: Annotated[str, Form()] = "",
    summary_short: Annotated[str, Form()] = "",
    summary_normal: Annotated[str, Form()] = "",
    summary_technical: Annotated[str, Form()] = "",
) -> Response:
    """Edit a card by hand — the name, type and the three descriptions. For the card's
    owner or an admin on a shared card, whether the AI succeeded or failed. Clears the
    "summary failed" note, re-indexes the words and re-embeds so search reflects the
    edit. A hand-written summary is non-empty, so the failed-summary retry leaves it be."""
    user_id = getattr(request.state, "user_id", None)
    is_admin = getattr(request.state, "is_admin", False)
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        mine = art.owner_user_id is not None and art.owner_user_id == user_id
        if not (mine or (is_admin and art.shared)):
            raise HTTPException(403)
        if name.strip():
            art.name = name.strip()[:200]
        if artifact_type.strip():
            art.artifact_type = artifact_type.strip()[:40]
        art.summary_short = summary_short.strip()
        art.summary_normal = summary_normal.strip()
        art.summary_technical = summary_technical.strip()
        art.summary_error = None
        art.summary_model = "manual"
        index_artifact_for_words(session, art)
        session.commit()
    # Re-embed with the edited text so semantic search matches it. Best-effort: no AI
    # key just means search leans on the old vector until the next pass.
    try:
        embed_model = build_embedding_model()
    except Exception:
        embed_model = None
    if embed_model is not None:
        try:
            with session_scope() as session:
                art = session.get(Artifact, artifact_id)
                if art is not None:
                    await embed_artifact(session, embed_model, art, force=True)
                    session.commit()
        finally:
            await embed_model.aclose()
    return RedirectResponse(f"/a/{artifact_id}", status_code=303)


@router.post("/artifact/{artifact_id}/rescan")
async def rescan_endpoint(request: Request, artifact_id: int) -> Response:
    """Force a fresh scan of one card. Allowed to the card's owner, or to an admin for
    a shared card. Awaits the rebuild, then returns to the (now refreshed) card."""
    user_id = getattr(request.state, "user_id", None)
    is_admin = getattr(request.state, "is_admin", False)
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        if art is None:
            raise HTTPException(404, i18n.msg(request, "err.artifact_not_found"))
        mine = art.owner_user_id is not None and art.owner_user_id == user_id
        if not (mine or (is_admin and art.shared)):
            raise HTTPException(403)
    await rescan_artifact(artifact_id)
    return RedirectResponse(f"/a/{artifact_id}", status_code=303)


@router.get("/recommend")
def recommend_redirect(task: str = "") -> RedirectResponse:
    """"What to pick?" is merged into search: one window for everything. The old link with a task
    we redirect straight into search, so bookmarks don't break.

    Recommendations haven't gone anywhere — they remain for ChatGPT via MCP, where the
    answer has room for explanations. On the site search already ranks by
    meaning, and a separate page only split "ask the program" in two."""
    q = f"?q={quote(task.strip())}" if task.strip() else ""
    return RedirectResponse(f"/{q}", status_code=308)


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        return templates.TemplateResponse(
            request, "help.html", {"counts": _counts(session, user_id), "nav": "help"}
        )


@router.get("/changes", response_class=HTMLResponse)
def changes_page(request: Request, kind: str = "", stale: str = "") -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        stale_mode = bool(stale)
        stale_items = ch.stale(session) if stale_mode else []
        oldest, newest = ch.oldest_and_newest(session)

        by_kind = {}
        for k in ("added", "updated", "removed", "renamed"):
            n = len(ch.recent(session, limit=9999, kind=k))
            if n:
                by_kind[k] = n

        return templates.TemplateResponse(
            request,
            "changes.html",
            {
                "items": ch.recent(session, limit=100, kind=kind) if not stale_mode else [],
                "kind": kind,
                "counts_by_kind": by_kind,
                "total": sum(by_kind.values()),
                "stale_mode": stale_mode,
                "stale_items": stale_items,
                "stale_count": len(ch.stale(session)),
                "stale_days": ch.STALE_DAYS,
                "nav": "changes",
                "oldest": oldest,
                "newest": newest,
                "counts": _counts(session, user_id),
            },
        )


# --- adding -------------------------------------------------------------
#
# Three steps, and the order here is not decoration:
#
#   1. what was given -> search, show candidates. We write nothing.
#   2. picked         -> show the plan: what will be created, how many files. We don't write.
#   3. clicked        -> we write.
#
# We never pull in automatically. A name by ear and from a picture is recognized
# imprecisely, the model sometimes invents an address — on a real reel it produced
# skills/last-30-day, which doesn't exist. The user decides, with their eyes.


def _add_page(request: Request, step: str, **extra) -> HTMLResponse:
    with session_scope() as session:
        user_id = getattr(request.state, "user_id", None)
        return templates.TemplateResponse(
            request,
            "add.html",
            {"step": step, "counts": _counts(session, user_id), "nav": "add", **extra},
        )


@router.get("/add", response_class=HTMLResponse)
def add_start(request: Request) -> HTMLResponse:
    return _add_page(request, "start")


@router.post("/add", response_class=HTMLResponse)
async def add_find(
    request: Request,
    source: Annotated[str, Form()] = "",
    file: Annotated[UploadFile | None, File()] = None,
) -> HTMLResponse:
    """Step 1: we parse whatever was given. We write nothing."""
    tmp: Path | None = None
    src = source.strip()

    if file is not None and file.filename:
        # We keep the extension: by it the finder tells a picture from a video.
        suffix = Path(file.filename).suffix or ".bin"
        data = await file.read()
        if len(data) > MAX_MEDIA_BYTES:
            return _add_page(
                request,
                "start",
                error=i18n.msg(request, "add.err.file_too_big", mb=MAX_MEDIA_BYTES // 1_000_000),
                source=src,
            )
        fd, name = tempfile.mkstemp(suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        tmp = Path(name)
        src = str(tmp)

    if not src:
        return _add_page(request, "start", error=i18n.msg(request, "add.err.need_input"))

    finder = Finder(github_token=settings.github_token)
    model = build_text_model() if settings.google_api_key else None
    try:
        result = await finder.find(src, model)
    except Exception as exc:
        return _add_page(
            request,
            "start",
            error=i18n.msg(request, "add.err.parse_failed", err=exc),
            source=source,
        )
    finally:
        await finder.aclose()
        if model is not None:
            await model.aclose()
        if tmp is not None:
            tmp.unlink(missing_ok=True)

    return _add_page(
        request,
        "found",
        result=result,
        given=file.filename if (file and file.filename) else source,
    )


@router.post("/add/plan", response_class=HTMLResponse)
async def add_plan(
    request: Request,
    url: Annotated[str, Form()],
    to: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Step 2: what exactly will be created. Still nothing is written."""
    fetcher = GitHubFetcher(token=settings.github_token)
    try:
        plan = await plan_import(fetcher, url, target_owner=to, target_name=name)
    except ImportError_ as exc:
        # A refusal can be useful: "this is a whole project, but here are the folders inside
        # that look like tools" — with links you can continue from.
        return _add_page(request, "refused", message=str(exc), url=url, to=to)
    except Exception as exc:
        return _add_page(
            request, "refused", message=i18n.msg(request, "add.err.failed", err=exc), url=url, to=to
        )
    finally:
        await fetcher.aclose()

    return _add_page(request, "plan", plan=plan, url=url, to=to)


@router.post("/add/run")
async def add_run(
    request: Request,
    url: Annotated[str, Form()],
    to: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
):
    """Step 3: we write. Only here and only on a click.

    We rebuild the plan from scratch rather than keeping it between steps. An
    extra archive download, but
    no stale plan: between "shown" and "clicked" the user could have gone off
    for tea, and meanwhile everything at the source changed.
    """
    if not settings.gitea_token:
        return _add_page(
            request, "refused", message=i18n.msg(request, "add.err.no_gitea_token"), url=url
        )

    fetcher = GitHubFetcher(token=settings.github_token)
    try:
        plan = await plan_import(fetcher, url, target_owner=to, target_name=name)
    except Exception as exc:
        await fetcher.aclose()
        return _add_page(request, "refused", message=str(exc), url=url, to=to)
    await fetcher.aclose()

    user_id = getattr(request.state, "user_id", None)
    provider = build_provider("gitea")
    text_model = build_text_model()
    embed_model = build_embedding_model()
    try:
        with session_scope() as session:
            result = await execute(session, provider, plan, settings.gitea_url)
            session.commit()

            repo = session.get(Repository, result.repository_id)
            await index_repository(session, provider, text_model, repo, force=True)

            # Private to the creator — BEFORE the first commit with this card: the import
            # goes into the shared Gitea, and index_repository would have marked it shared. Between
            # that commit and the AI description (seconds) it would hang visible to everyone.
            art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
            art.owner_user_id = user_id
            art.shared = False
            session.commit()

            record_upstream(session, art.id, plan)
            await embed_artifact(session, embed_model, art)
            await tag_artifact(session, art, text_model)
            index_artifact_for_words(session, art)
            # Automatically pick a private folder by meaning — the user is left to adjust it.
            auto_cid = _auto_category(session, art, user_id)
            if auto_cid is not None:
                session.add(ArtifactCategory(artifact_id=art.id, category_id=auto_cid))
            session.commit()
            card = {
                "id": art.id,
                "name": art.name,
                "owner": art.repository.owner,
                "summary_short": art.summary_short,
                "preview_url": preview_url(art),
            }
    except Exception as exc:
        return _add_page(
            request, "refused", message=i18n.msg(request, "add.err.failed", err=exc), url=url, to=to
        )
    finally:
        await provider.aclose()
        await text_model.aclose()
        await embed_model.aclose()

    return _add_page(request, "done", card=card)


def _create_draft(session, user_id, source_url: str, name: str, summary: str, heard: str) -> int:
    """A draft: a card without an import from GitHub. When a link or a video
    couldn't be reduced to a repository, we save what was recognized — to process
    later. Lives in a separate "Drafts" source, private to the creator."""
    src = get_or_create_source(session, "draft", "", "Drafts")
    key = source_url or name or heard or "draft"
    ext = "draft-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:16]  # noqa: S324

    repo = session.scalar(
        select(Repository).where(Repository.source_id == src.id, Repository.external_id == ext)
    )
    if repo is None:
        repo = Repository(
            source_id=src.id,
            external_id=ext,
            owner="draft",
            name=(name or "draft")[:256],
            default_branch="",
            html_url="",
            original_url=source_url or "",
        )
        session.add(repo)
        session.flush()

    art = session.scalar(select(Artifact).where(Artifact.repository_id == repo.id))
    if art is None:
        art = Artifact(
            repository_id=repo.id,
            name=(name or "draft")[:256],
            artifact_type="draft",
            summary_short=summary or "",
            doc_text=heard or "",
            owner_user_id=user_id,
            shared=False,
        )
        session.add(art)
        session.flush()
    else:
        art.name = (name or art.name)[:256]
        art.summary_short = summary or art.summary_short
        art.owner_user_id = user_id
        art.shared = False
    index_artifact_for_words(session, art)
    return art.id


@router.post("/add/draft")
def add_draft(
    request: Request,
    source: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    summary: Annotated[str, Form()] = "",
    heard: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Didn't reduce to GitHub — we make a draft and lead to the same zone choice."""
    user_id = getattr(request.state, "user_id", None)
    with session_scope() as session:
        aid = _create_draft(
            session, user_id, source.strip(), name.strip(), summary.strip(), heard.strip()
        )
        art = session.get(Artifact, aid)
        card = {
            "id": art.id,
            "name": art.name,
            "owner": art.repository.owner,
            "summary_short": art.summary_short,
            "preview_url": preview_url(art),
        }
    return _add_page(request, "done", card=card)


@router.post("/add/save")
def add_save(
    request: Request,
    artifact_id: Annotated[int, Form()],
    zone: Annotated[str, Form()] = "shared",
) -> RedirectResponse:
    """The final step of creation: the card is marked private or shared and saved.
    Until then it's the creator's private draft."""
    user_id = getattr(request.state, "user_id", None)
    with session_scope() as session:
        art = session.get(Artifact, artifact_id)
        # The zone is set by the draft's owner (or an ownerless one — then they become it).
        if art is not None and art.owner_user_id in (user_id, None):
            art.owner_user_id = user_id
            art.shared = zone != "private"
    return RedirectResponse("/", status_code=303)
