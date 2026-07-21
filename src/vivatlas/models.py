"""Database tables."""

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Source(Base):
    """Connection to a host: Gitea, later GitHub."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(default=True)

    # Zone. Empty — shared: every tool is visible to everyone. User set — private:
    # the source and its tools are visible only to that user. This way everyone has
    # their own repositories, without mixing with the shared ones.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Access token for a private source — encrypted (like the 2FA secret). Shared
    # sources have it empty: they run under the shared keys from .env. Never stored
    # in plaintext anywhere: steal the database and you steal ciphertext, not the token.
    token_enc: Mapped[str] = mapped_column(String(512), default="")

    # When the source was last crawled automatically (the daily background
    # scan). Empty — never yet. It also serves as a "lock": the server that first
    # stamps a fresh mark is the one that does the crawl; the second sees it fresh and skips.
    last_auto_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repositories: Mapped[list["Repository"]] = relationship(back_populates="source")

    __table_args__ = (UniqueConstraint("kind", "base_url", name="uq_source"),)


class Repository(Base):
    """Repository. Private ones don't end up here — see scanner.py."""

    __tablename__ = "repositories"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(256))
    default_branch: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    html_url: Mapped[str] = mapped_column(String(512), default="")
    clone_url: Mapped[str] = mapped_column(String(512), default="")
    size_kb: Mapped[int] = mapped_column(Integer, default=0)
    is_archived: Mapped[bool] = mapped_column(default=False)
    is_empty: Mapped[bool] = mapped_column(default=False)
    # Gitea fills this in if the repository was brought in from somewhere.
    original_url: Mapped[str] = mapped_column(String(512), default="")

    remote_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scanned_commit: Mapped[str | None] = mapped_column(String(64))
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Set when the repository disappears from the host's listing.
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The user deleted the card for good. Kept on the repository, not just on the
    # card (the card is already gone): without this the next scan would see the
    # repository alive and build the card again — "delete forever" would turn into
    # "delete until tomorrow". The scan skips such a repository, not reviving it.
    user_removed: Mapped[bool] = mapped_column(default=False, index=True)

    source: Mapped[Source] = relationship(back_populates="repositories")

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_repo_external"),)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class Artifact(Base):
    """Tool card. For now one repository = one card."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), unique=True)

    name: Mapped[str] = mapped_column(String(256))
    artifact_type: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(default=0.0)
    detect_reasons: Mapped[str] = mapped_column(Text, default="")

    # STALE FIELD. The card used to live in exactly one folder — this FK. Now
    # folder membership is stored by the ArtifactCategory table (many-to-many): in
    # both the shared and the private folders at once. The migration moves the old
    # value into ArtifactCategory; new code does NOT read or write this field.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )

    # Private card: if a user is set, only they see it (private zone). Empty —
    # shared, everyone sees it. Set at creation (private/shared) on top of the
    # source's zone: the import comes from the shared Gitea, and it's exactly this
    # mark that makes a card private.
    #
    # STALE FIELD. This one mark used to decide both "who owns" and "who sees".
    # Now this is split into owner_user_id (owner, forever) and shared (whether
    # everyone sees it). The field is kept so as not to break the schema and so the
    # migration can derive the new ones from it. New code does NOT read or write it
    # — see filters.visible_ids.
    private_to_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    # Who created the card. Forever: even once it's shared, the owner remains —
    # only they (or an administrator) can delete it, and only they see it among
    # "mine". Empty — a shared seed/scan without an owner (only an administrator
    # touches it). Separate from "whether everyone sees it" (shared): ownership and
    # visibility are two different facts, and they used to be conflated in one field.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    # Whether everyone sees the card. True — in the shared catalogue, visible to
    # all. False — only to the owner (private). The owner toggles it; an
    # administrator can only take one that's already shared out (unshare). Ownership
    # isn't lost in the process — unlike the old "publish", which nulled out
    # private_to_user_id.
    #
    # False by default — safe: a card left without an explicit shared is better
    # shown to no one than to everyone. "Shared" is assigned explicitly where it
    # fits: building a card from a SHARED source (indexer), the owner's "make
    # shared" button. Private sources and additions stay private.
    shared: Mapped[bool] = mapped_column(default=False, index=True)

    # Hidden from the catalogue, but NOT deleted (the data and the link to the
    # source are intact). This is how the initial "seed" batch is marked: we show
    # only what the user added themselves (by import, draft, or their own source with a token).
    hidden: Mapped[bool] = mapped_column(default=False, index=True)

    # "New arrival": the card was added by a recent scan (manual or daily
    # background) and the user hasn't opened it yet. We show a "new" badge so that
    # at first glance it's clear new repositories were pulled in. Goes out when
    # the card is opened.
    is_new: Mapped[bool] = mapped_column(default=False, index=True)

    anchor_path: Mapped[str | None] = mapped_column(String(512))
    preview_path: Mapped[str | None] = mapped_column(String(512))
    doc_text: Mapped[str] = mapped_column(Text, default="")
    file_count: Mapped[int] = mapped_column(Integer, default=0)
    file_paths: Mapped[str] = mapped_column(Text, default="")  # JSON list of paths

    summary_short: Mapped[str] = mapped_column(Text, default="")
    summary_normal: Mapped[str] = mapped_column(Text, default="")
    summary_technical: Mapped[str] = mapped_column(Text, default="")
    summary_model: Mapped[str | None] = mapped_column(String(64))
    summary_error: Mapped[str | None] = mapped_column(Text)

    # Which commit the card was built from. Matches — no need to rebuild.
    source_commit: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    repository: Mapped[Repository] = relationship()


class Embedding(Base):
    """The card as numbers — for search by meaning.

    The model and dimension are stored in each row: numbers from different models
    can't be compared, so when the model changes rows aren't rewritten, but new
    ones are added.
    """

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    model: Mapped[str] = mapped_column(String(64))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    source_hash: Mapped[str] = mapped_column(String(64))  # text unchanged — don't recompute
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("artifact_id", "model", name="uq_embedding"),)


class UpstreamLink(Base):
    """Where the tool came from and how it relates to its source.

    The key thing here is baseline_*: snapshots at the moment we first saw the
    copy and the source. Without them "the files differ" means nothing — it's
    unclear whether a new version came out or the user edited it themselves.
    """

    __tablename__ = "upstream_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), unique=True)

    kind: Mapped[str] = mapped_column(String(32))  # github-file | gitea-mirror
    upstream_repo: Mapped[str] = mapped_column(String(256))
    upstream_path: Mapped[str] = mapped_column(String(512), default="")
    upstream_url: Mapped[str] = mapped_column(String(512), default="")
    discovered_by: Mapped[str] = mapped_column(String(64), default="")

    # A mark: how everything looked when the copy and the source matched.
    baseline_local_sha: Mapped[str] = mapped_column(String(64), default="")
    baseline_upstream_sha: Mapped[str] = mapped_column(String(64), default="")
    baseline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_local_sha: Mapped[str] = mapped_column(String(64), default="")
    last_upstream_sha: Mapped[str] = mapped_column(String(64), default="")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    check_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    artifact: Mapped["Artifact"] = relationship()


class Tag(Base):
    """Tag dictionary."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="other")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArtifactTag(Base):
    """A tag on a card.

    source says where it came from:
      derived — derived from the path, name, files. A rule, always the same.
      ai      — suggested by the model.
      manual  — set by a user. These are never overwritten.
    """

    __tablename__ = "artifact_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"))

    source: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(default=1.0)
    origin: Mapped[str] = mapped_column(String(64), default="")  # rule or model
    manually_confirmed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tag: Mapped[Tag] = relationship()

    __table_args__ = (UniqueConstraint("artifact_id", "tag_id", name="uq_artifact_tag"),)


class TagSuppression(Base):
    """A ban on a tag for a card.

    The user removed an auto-tag — a record is written here, and the tag will
    never come back on rescanning. Without this table the removal would be
    pointless: the next run would put the tag back.
    """

    __tablename__ = "tag_suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tag: Mapped[Tag] = relationship()

    __table_args__ = (UniqueConstraint("artifact_id", "tag_id", name="uq_suppression"),)


class Change(Base):
    """A single event: something appeared, changed, or vanished.

    Written the moment it's noticed, not computed after the fact. Once a
    repository is deleted, there'll be nowhere left to learn that it existed.
    """

    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # added|updated|removed|renamed
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    artifact_id: Mapped[int | None] = mapped_column(ForeignKey("artifacts.id"))
    scan_run_id: Mapped[int | None] = mapped_column(ForeignKey("scan_runs.id"))

    title: Mapped[str] = mapped_column(String(512), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repository: Mapped[Repository] = relationship()
    artifact: Mapped["Artifact | None"] = relationship()


class ScanRun(Base):
    """A single scan: when, how many were found, what was skipped."""

    __tablename__ = "scan_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="running")

    repos_seen: Mapped[int] = mapped_column(Integer, default=0)
    repos_added: Mapped[int] = mapped_column(Integer, default=0)
    repos_updated: Mapped[int] = mapped_column(Integer, default=0)
    repos_gone: Mapped[int] = mapped_column(Integer, default=0)
    repos_skipped_private: Mapped[int] = mapped_column(Integer, default=0)

    error: Mapped[str | None] = mapped_column(Text)


# --- the door --------------------------------------------------------------
#
# Everything below appeared for one reason: the program is going to be exposed
# to the outside through a tunnel. Until now it stood at home and needed no lock.
# Now it does, and there's nothing here "for growth" — only what a door can't be
# a door without.


class User(Base):
    """A person who can sign in.

    The password is never stored anywhere — only its hash. Even we can't learn a
    user's password, and this isn't a convenience but a requirement: the database
    can be stolen, and then hashes are stolen, not passwords.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The email is stored in lowercase: Boris@ and boris@ are one person, and
    # otherwise one mailbox would get two accounts.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")

    password_hash: Mapped[str] = mapped_column(String(255))

    # The first to sign in becomes the owner. The owner decides whether to let
    # others in: people install the program for themselves, and open registration
    # by default would mean any passerby could create an account in someone else's catalogue.
    is_owner: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Default avatar from the "busts" set (static/usericons/<key>.webp). Shown if
    # the user hasn't uploaded their own photo. On creation a random one is
    # assigned; it can be changed in settings. Empty — only on very old rows from
    # before the migration (migrate will assign them a random one).
    avatar_preset: Mapped[str] = mapped_column(String(32), default="")

    # Two-step verification. The secret is stored encrypted here: it's equivalent
    # to a second password, and in plaintext it defeats the whole point.
    totp_secret_enc: Mapped[str] = mapped_column(String(512), default="")
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The last accepted code. The same code lives for 30 seconds, and without this
    # mark a glimpsed code could be entered a second time within the same window.
    totp_last_code: Mapped[str] = mapped_column(String(16), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Protection against brute force. We count failures and lock for a while.
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    backup_codes: Mapped[list["BackupCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """An open session — what makes a sign-in a sign-in.

    Kept in the database, not just in a signed cookie: otherwise "sign out on all
    devices" is fundamentally impossible — a signed cookie can't be revoked.

    The database holds the hash of the key, not the key itself. Whoever steals the
    database won't get ready-made passes.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # For the "where am I signed in" page: a user must see someone else's sign-in.
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="sessions")


class BackupCode(Base):
    """A backup code — in case the phone is lost.

    Stored as a hash. Shown to the user exactly once, when issued: if the code can
    be glimpsed in the database or on the page later, it's no protection.

    Single-use: a used one is marked and no longer works.
    """

    __tablename__ = "backup_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="backup_codes")


class Avatar(Base):
    """Profile photo — already in webp. A separate table rather than a column in
    users: the user row is read on every request, and there's no point dragging
    tens of kilobytes of image along with it for nothing. Here it's fetched only when displayed."""

    __tablename__ = "avatars"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    webp: Mapped[bytes] = mapped_column(LargeBinary)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Invite(Base):
    """An invitation. The owner invites; not just anyone walks in on their own."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), default="")  # empty — for anyone
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Setting(Base):
    """Program settings that the owner changes from the interface.

    Not in .env: .env is read at startup, while these things change on the fly and
    survive a restart.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Category(Base):
    """A category-folder for the catalogue.

    Shared (owner_user_id empty): maintained by an administrator, seen by all, one
    order — it's that shared catalogue. Private (owner_user_id set): one person's
    personal folder, seen and arranged only by them; the administrator doesn't see
    it (privacy). Auto-categories (types) aren't written here — every card has them
    anyway; here only one's own, created by hand."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))  # as entered (source)
    # Translations of the name into three languages (JSON {"en","ru","he"}) —
    # filled automatically on creation/rename. Empty/no language — we show the
    # original name. This way a folder is shown translated: Design/עיצוב.
    names_json: Mapped[str] = mapped_column(Text, default="")
    icon: Mapped[str] = mapped_column(String(32), default="")  # key from caticons
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Owner of a private folder. Empty — shared (admin's). CASCADE: delete a user
    # and their private folders go along with them.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    __table_args__ = (
        # The name is unique within an owner: two people can each create their own
        # "Design" folder. SQLite treats NULLs as distinct, so uniqueness of SHARED
        # names (owner empty) is held by the separate partial index below.
        UniqueConstraint("owner_user_id", "name", name="uq_category_owner_name"),
        Index(
            "uq_shared_category_name",
            "name",
            unique=True,
            sqlite_where=text("owner_user_id IS NULL"),
        ),
    )


class ArtifactCategory(Base):
    """A card in a folder (many-to-many). It replaced the single
    Artifact.category_id: one card can live both in a shared folder and in
    different people's private folders — each has its own row. Membership in
    someone else's private folder isn't visible to others (privacy rests on the
    owner_user_id of the Category itself)."""

    __tablename__ = "artifact_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    # CASCADE (not SET NULL, as it was for the stale category_id): delete a folder
    # and the memberships go with it, the cards remain.
    category_id: Mapped[int] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (
        UniqueConstraint("artifact_id", "category_id", name="uq_artifact_category"),
    )


class Favorite(Base):
    """A favourited card. Everyone has their own: favourites are a personal matter,
    not a shared property of the tool, so it's tied to a user."""

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("user_id", "artifact_id", name="uq_favorite"),)


class RemovedNotice(Base):
    """A "this went away" notice. When the owner deletes a card that someone kept
    in their favourites, it can't be allowed to vanish silently on them: a
    favourite is a link, not a copy, and the person had a right to know it was deleted.

    Written at the moment of deletion — the card itself will be gone, so we store
    the name as a separate string, not a reference to it. Goes out when the person has closed it."""

    __tablename__ = "removed_notices"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    artifact_name: Mapped[str] = mapped_column(String(256))
    removed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
