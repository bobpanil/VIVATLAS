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

    # Зона. Пусто — общая: инструменты видят все. Задан пользователь — частная:
    # источник и его инструменты видны только ему. Так у каждого свои
    # репозитории, не смешиваясь с общими.
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

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
    # Gitea заполняет, если репозиторий привезён откуда-то.
    original_url: Mapped[str] = mapped_column(String(512), default="")

    remote_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    # Своя категория-папка, если карточку туда положили. Пусто — лежит только
    # под своим типом (автокатегорией). При удалении категории обнуляется.
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL")
    )

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


class UpstreamLink(Base):
    """Откуда взят инструмент и как он соотносится с источником.

    Ключевое здесь — baseline_*: слепки на момент, когда мы впервые увидели
    копию и источник. Без них "файлы разные" ничего не значит — непонятно, то
    ли вышла новая версия, то ли пользователь сам поправил.
    """

    __tablename__ = "upstream_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    artifact_id: Mapped[int] = mapped_column(ForeignKey("artifacts.id"), unique=True)

    kind: Mapped[str] = mapped_column(String(32))  # github-file | gitea-mirror
    upstream_repo: Mapped[str] = mapped_column(String(256))
    upstream_path: Mapped[str] = mapped_column(String(512), default="")
    upstream_url: Mapped[str] = mapped_column(String(512), default="")
    discovered_by: Mapped[str] = mapped_column(String(64), default="")

    # Отметка: как всё выглядело, когда копия и источник совпадали.
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


class Change(Base):
    """Одно событие: что-то появилось, изменилось или пропало.

    Пишется в момент, когда замечено, а не вычисляется задним числом. После
    удаления репозитория узнать, что он был, будет уже неоткуда.
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


# --- дверь -----------------------------------------------------------------
#
# Всё, что ниже, появилось ради одного: программу собираются выставить наружу
# через туннель. До этого она стояла дома и замка ей не требовалось. Теперь
# требуется, и здесь нет ничего "на вырост" — только то, без чего дверь не
# дверь.


class User(Base):
    """Человек, который может войти.

    Пароль не хранится нигде и никогда — только его хеш. Даже мы не можем
    узнать пароль пользователя, и это не удобство, а требование: базу могут
    украсть, и тогда украдут хеши, а не пароли.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Почта хранится в нижнем регистре: Boris@ и boris@ — один человек, и
    # иначе на один ящик заведут два аккаунта.
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")

    password_hash: Mapped[str] = mapped_column(String(255))

    # Первый вошедший становится хозяином. Хозяин решает, пускать ли других:
    # программу ставят себе сами, и открытая регистрация по умолчанию означала
    # бы, что любой прохожий заводит аккаунт в чужом каталоге.
    is_owner: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)

    # Двухэтапная проверка. Секрет здесь лежит зашифрованным: он равносилен
    # второму паролю, и в открытом виде обесценивает всю затею.
    totp_secret_enc: Mapped[str] = mapped_column(String(512), default="")
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Последний принятый код. Один и тот же код живёт 30 секунд, и без этой
    # отметки подсмотренный код можно ввести второй раз в то же окно.
    totp_last_code: Mapped[str] = mapped_column(String(16), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Защита от перебора. Считаем неудачи и запираем на время.
    failed_logins: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    backup_codes: Mapped[list["BackupCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """Открытая сессия — то, что делает вход входом.

    Держим в базе, а не только в подписанной куке: иначе «выйти на всех
    устройствах» невозможно в принципе — подписанную куку не отозвать.

    В базе лежит хеш ключа, а не сам ключ. Кто украдёт базу, не получит
    готовых пропусков.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    # Для страницы «где я вошёл»: человек должен видеть чужой вход.
    user_agent: Mapped[str] = mapped_column(String(256), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User"] = relationship(back_populates="sessions")


class BackupCode(Base):
    """Код восстановления — на случай потери телефона.

    Хранится хешем. Показывается человеку ровно один раз, при выдаче: если
    код можно подсмотреть в базе или на странице позже, он не защита.

    Одноразовый: использованный помечается и больше не подходит.
    """

    __tablename__ = "backup_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code_hash: Mapped[str] = mapped_column(String(255))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    user: Mapped["User"] = relationship(back_populates="backup_codes")


class Invite(Base):
    """Приглашение. Хозяин зовёт, а не всякий заходит сам."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), default="")  # пусто — для кого угодно
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class Setting(Base):
    """Настройки программы, которые меняет хозяин из интерфейса.

    Не в .env: .env читается при запуске, а эти вещи меняются на ходу и
    переживают перезапуск.
    """

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class Category(Base):
    """Категория-папка для каталога. Общая: владелец раскладывает инструменты,
    и все видят один порядок. Автокатегории (типы) сюда не пишутся — они и так
    есть у каждой карточки; здесь только свои, заведённые руками."""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Favorite(Base):
    """Избранная карточка. У каждого своя: избранное — личное дело, а не общее
    свойство инструмента, поэтому привязано к пользователю."""

    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    __table_args__ = (UniqueConstraint("user_id", "artifact_id", name="uq_favorite"),)
