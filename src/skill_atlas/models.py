"""Таблицы базы."""

from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
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
    file_paths: Mapped[str] = mapped_column(Text, default="")  # JSON-список путей

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


class Embedding(Base):
    """Карточка в виде чисел — для поиска по смыслу.

    Модель и размерность хранятся в каждой строке: числа от разных моделей
    сравнивать нельзя, поэтому при смене модели строки не переписываются, а
    добавляются новые.
    """

    __tablename__ = "embeddings"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    model: Mapped[str] = mapped_column(String(64))
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[bytes] = mapped_column(LargeBinary)
    source_hash: Mapped[str] = mapped_column(String(64))  # текст не менялся — не пересчитываем
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("artifact_id", "model", name="uq_embedding"),)


class Tag(Base):
    """Словарь тегов."""

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    category: Mapped[str] = mapped_column(String(32), default="other")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ArtifactTag(Base):
    """Тег на карточке.

    source говорит, откуда он взялся:
      derived — вывели из пути, имени, файлов. Правило, всегда одинаково.
      ai      — предложила модель.
      manual  — поставил человек. Такие никогда не перезаписываются.
    """

    __tablename__ = "artifact_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"))

    source: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(default=1.0)
    origin: Mapped[str] = mapped_column(String(64), default="")  # правило или модель
    manually_confirmed: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tag: Mapped[Tag] = relationship()

    __table_args__ = (UniqueConstraint("artifact_id", "tag_id", name="uq_artifact_tag"),)


class TagSuppression(Base):
    """Запрет тега на карточке.

    Пользователь удалил автотег — сюда пишется запись, и тег больше никогда не
    вернётся при пересканировании. Без этой таблицы удаление было бы
    бессмысленным: следующий прогон поставил бы тег обратно.
    """

    __tablename__ = "tag_suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"))
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    tag: Mapped[Tag] = relationship()

    __table_args__ = (UniqueConstraint("artifact_id", "tag_id", name="uq_suppression"),)


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
