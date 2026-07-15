"""Таблицы базы."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Source(Base):
    """Подключение к хостингу: Gitea, позже GitHub."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    display_name: Mapped[str] = mapped_column(String(128))
    base_url: Mapped[str] = mapped_column(String(512))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    repositories: Mapped[list["Repository"]] = relationship(back_populates="source")

    __table_args__ = (UniqueConstraint("kind", "base_url", name="uq_source"),)


class Repository(Base):
    """Репозиторий. Приватные сюда не попадают — см. scanner.py."""

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

    remote_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scanned_commit: Mapped[str | None] = mapped_column(String(64))
    last_scanned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Проставляется, когда репозиторий пропал из выдачи хостинга.
    gone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[Source] = relationship(back_populates="repositories")

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_repo_external"),)

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class Artifact(Base):
    """Карточка инструмента. Пока один репозиторий = одна карточка."""

    __tablename__ = "artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), unique=True)

    name: Mapped[str] = mapped_column(String(256))
    artifact_type: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(default=0.0)
    detect_reasons: Mapped[str] = mapped_column(Text, default="")

    anchor_path: Mapped[str | None] = mapped_column(String(512))
    preview_path: Mapped[str | None] = mapped_column(String(512))
    doc_text: Mapped[str] = mapped_column(Text, default="")
    file_count: Mapped[int] = mapped_column(Integer, default=0)

    summary_short: Mapped[str] = mapped_column(Text, default="")
    summary_normal: Mapped[str] = mapped_column(Text, default="")
    summary_technical: Mapped[str] = mapped_column(Text, default="")
    summary_model: Mapped[str | None] = mapped_column(String(64))
    summary_error: Mapped[str | None] = mapped_column(Text)

    # По какому коммиту собрана карточка. Совпал — пересобирать не надо.
    source_commit: Mapped[str | None] = mapped_column(String(64))
    content_hash: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )

    repository: Mapped[Repository] = relationship()


class ScanRun(Base):
    """Одно сканирование: когда, сколько нашли, что пропустили."""

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
