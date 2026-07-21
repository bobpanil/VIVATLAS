"""Rights and visibility of category folders: shared (admin) and personal (per-user).

A single source of truth, so privacy can't be forgotten on some screen:

- SHARED folder (``owner_user_id`` empty) — part of the shared catalogue: everyone
  sees it, only the administrator manages it (creates/renames/deletes).
- PERSONAL folder (``owner_user_id`` set) — one user's private matter: only they
  see it, manage it, and file cards into it. Even the administrator does NOT see it.

A card's membership in a folder lives in ``ArtifactCategory`` (many-to-many): one
card can sit in a shared folder and in personal folders of different users — each
gets their own row. We never show one user's personal membership to others.
"""

from sqlalchemy import Select, select

from vivatlas.models import Artifact, Category


def visible_category_ids(user_id: int | None) -> Select:
    """ids of folders this user is entitled to see: all shared + their own personal.
    Anonymous — shared only."""
    cond = Category.owner_user_id.is_(None)
    if user_id is not None:
        cond = cond | (Category.owner_user_id == user_id)
    return select(Category.id).where(cond)


def can_view(cat: Category, user_id: int | None) -> bool:
    """Whether the user sees this folder: shared — yes; personal — only its owner."""
    return cat.owner_user_id is None or (
        user_id is not None and cat.owner_user_id == user_id
    )


def can_manage(cat: Category, user_id: int | None, is_admin: bool) -> bool:
    """Who may edit/delete/reorder a folder: shared — the administrator;
    personal — its owner. Someone else's personal — nobody (it isn't even visible)."""
    if user_id is None:
        return False
    if cat.owner_user_id is None:
        return is_admin
    return cat.owner_user_id == user_id


def can_file(art: Artifact, cat: Category, user_id: int | None, is_admin: bool) -> bool:
    """Whether card ``art`` may be filed into folder ``cat`` (or removed).

    - into a SHARED folder: ONLY the administrator files cards, and only a shared
      card. Users don't "configure" shared folders — they share a card
      (make it shared) and it appears for everyone in the catalogue; how to arrange
      the shared catalogue into folders is the administrator's call;
    - into their OWN PERSONAL folder: any card the user is entitled to SEE (a personal
      folder is a private shelf on top of the catalogue, like favourites);
    - into someone else's personal folder: never.

    Visibility of ``art`` itself is checked by the caller (before this call)."""
    if user_id is None:
        return False
    if cat.owner_user_id is None:  # shared folder — administrator only
        return is_admin and art.shared
    return cat.owner_user_id == user_id  # their own personal
