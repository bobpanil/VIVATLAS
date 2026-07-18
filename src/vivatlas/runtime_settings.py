"""Настройки, которые меняет хозяин на ходу, — поверх таблицы settings.

Не в .env: .env читается один раз при запуске, а это (почта, адрес сайта,
открыта ли регистрация) хозяин правит из панели, и правка должна пережить
перезапуск, не требуя доступа к файлу на сервере.

Пароль SMTP — чужой секрет, как токены Gitea: в базе лежит зашифрованным
(Fernet на главном ключе), наружу выходит только замаскированным. В открытом
виде не хранится нигде.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from vivatlas import security
from vivatlas.models import Setting

# Ключи в таблице settings. Собраны здесь, чтобы не рассыпать строковые литералы
# по коду: опечатка в ключе — это молча потерянная настройка.
SMTP_HOST = "smtp_host"
SMTP_PORT = "smtp_port"
SMTP_SECURITY = "smtp_security"  # none | starttls | ssl
SMTP_USERNAME = "smtp_username"
SMTP_PASSWORD_ENC = "smtp_password_enc"
SMTP_FROM = "smtp_from"
SMTP_FROM_NAME = "smtp_from_name"
SITE_URL = "site_url"
REGISTRATION_OPEN = "registration_open"

_SECURITY_MODES = ("none", "starttls", "ssl")


# --- сырой доступ к паре ключ/значение -------------------------------------


def get(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row is not None else default


def set(session: Session, key: str, value: str) -> None:
    """Записать значение. Заводит строку, если её ещё нет (upsert)."""
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def get_bool(session: Session, key: str, default: bool) -> bool:
    row = session.get(Setting, key)
    if row is None:
        return default
    return row.value == "1"


def set_bool(session: Session, key: str, value: bool) -> None:
    set(session, key, "1" if value else "0")


def get_int(session: Session, key: str, default: int) -> int:
    row = session.get(Setting, key)
    if row is None or not row.value:
        return default
    try:
        return int(row.value)
    except ValueError:
        return default


# --- адрес сайта (для ссылок в письмах) ------------------------------------


def site_url(session: Session) -> str:
    """Адрес, с которого собираются ссылки в письмах (сброс пароля и т.п.).

    За туннелем настоящий внешний адрес программе изнутри не виден, а http-адрес
    из запроса ведёт на сервер, а не на домен. Поэтому адрес задаёт хозяин; если
    не задан — зовущий подставит адрес из запроса как запасной.
    """
    return get(session, SITE_URL, "").strip().rstrip("/")


# --- открыта ли регистрация ------------------------------------------------
#
# По умолчанию открыта. Хозяин может закрыть из панели — тогда только по
# приглашению или заведением из панели. Читается тут, чтобы и страница
# регистрации, и её ссылка спрашивали одно и то же место.


def registration_open(session: Session) -> bool:
    return get_bool(session, REGISTRATION_OPEN, default=True)


# --- SMTP ------------------------------------------------------------------


@dataclass(frozen=True)
class SmtpConfig:
    host: str = ""
    port: int = 587
    security: str = "starttls"  # none | starttls | ssl
    username: str = ""
    password: str = ""  # расшифрованный; наружу отдаём только через mask
    from_addr: str = ""
    from_name: str = "VivAtlas"

    @property
    def is_configured(self) -> bool:
        """Хватает ли данных, чтобы вообще пытаться отправить.

        Достаточно узла и обратного адреса. Логин/пароль не требуем: бывают
        внутренние ретрансляторы без входа. Отсутствие узла — не «ошибка», а
        «почта ещё не настроена», и звать отправку тогда просто незачем.
        """
        return bool(self.host and self.effective_from)

    @property
    def effective_from(self) -> str:
        """Обратный адрес. Не задан явно — берём логин, он обычно и есть адрес."""
        return (self.from_addr or self.username).strip()


def get_smtp(session: Session) -> SmtpConfig:
    """Собрать настройки почты из базы. Пароль расшифровывается здесь."""
    return SmtpConfig(
        host=get(session, SMTP_HOST, "").strip(),
        port=get_int(session, SMTP_PORT, 587),
        security=_clean_security(get(session, SMTP_SECURITY, "starttls")),
        username=get(session, SMTP_USERNAME, "").strip(),
        password=security.decrypt_secret(get(session, SMTP_PASSWORD_ENC, "")),
        from_addr=get(session, SMTP_FROM, "").strip(),
        from_name=get(session, SMTP_FROM_NAME, "VivAtlas").strip() or "VivAtlas",
    )


def save_smtp(
    session: Session,
    *,
    host: str,
    port: int,
    security_mode: str,
    username: str,
    from_addr: str,
    from_name: str,
    password: str | None = None,
) -> None:
    """Сохранить настройки почты.

    Пароль трогаем ТОЛЬКО если передана непустая строка: пустое поле в форме
    означает «оставить прежний», а не «стереть» — иначе каждое сохранение
    других полей молча обнуляло бы пароль. Ровно как со своими токенами
    источников в настройках.
    """
    set(session, SMTP_HOST, host.strip())
    set(session, SMTP_PORT, str(int(port)))
    set(session, SMTP_SECURITY, _clean_security(security_mode))
    set(session, SMTP_USERNAME, username.strip())
    set(session, SMTP_FROM, from_addr.strip())
    set(session, SMTP_FROM_NAME, from_name.strip() or "VivAtlas")
    if password:
        set(session, SMTP_PASSWORD_ENC, security.encrypt_secret(password))


def smtp_password_mask(session: Session) -> str:
    """Замаскированный пароль SMTP — только чтобы человек видел, что он задан."""
    plain = security.decrypt_secret(get(session, SMTP_PASSWORD_ENC, ""))
    return security.mask_secret(plain)


def _clean_security(mode: str) -> str:
    mode = (mode or "").strip().lower()
    return mode if mode in _SECURITY_MODES else "starttls"


# --- операционная конфигурация поверх .env ---------------------------------
#
# Ключи из .env, которые хозяин правит из админки. Храним переопределения в
# таблице settings (секреты — шифром) и НАКЛАДЫВАЕМ их на глобальный объект
# settings при старте и после каждого сохранения. Так весь код, который читает
# settings.gitea_token и т.п., подхватывает правку без перезапуска и без правки
# самих потребителей. Пусто в базе — возвращаем значение из .env.
#
# SECRET_KEY и DATABASE_URL сюда НЕ входят намеренно: на них держится вся
# расшифровка и само подключение — менять их на ходу из UI опасно.

CFG_GITEA_URL = "cfg_gitea_url"
CFG_GITEA_TOKEN = "cfg_gitea_token"  # секрет
CFG_GITHUB_TOKEN = "cfg_github_token"  # секрет
CFG_GOOGLE_KEY = "cfg_google_api_key"  # секрет
CFG_LLM_MODEL = "cfg_llm_model"
CFG_EMBEDDING_MODEL = "cfg_embedding_model"

# ключ настройки -> (атрибут settings, секрет ли)
_CONFIG_MAP: dict[str, tuple[str, bool]] = {
    CFG_GITEA_URL: ("gitea_url", False),
    CFG_GITEA_TOKEN: ("gitea_token", True),
    CFG_GITHUB_TOKEN: ("github_token", True),
    CFG_GOOGLE_KEY: ("google_api_key", True),
    CFG_LLM_MODEL: ("llm_model", False),
    CFG_EMBEDDING_MODEL: ("embedding_model", False),
}

_env_defaults: dict[str, str] = {}


def _capture_env_defaults() -> None:
    """Запомнить значения из .env ОДИН раз — до первого наложения правок. Нужны,
    чтобы при очистке правки вернуться к .env, а не к пустоте."""
    if _env_defaults:
        return
    from vivatlas.config import settings

    for key, (attr, _secret) in _CONFIG_MAP.items():
        _env_defaults[key] = getattr(settings, attr) or ""


def apply_config_overrides(session: Session) -> None:
    """Наложить правки из базы на глобальный settings. Зовётся при старте и после
    сохранения в админке."""
    from vivatlas.config import settings

    _capture_env_defaults()
    for key, (attr, secret) in _CONFIG_MAP.items():
        raw = get(session, key, "")
        if raw:
            value = security.decrypt_secret(raw) if secret else raw
            # Секрет не расшифровался (сменили SECRET_KEY) — не затираем пустым
            # рабочее значение из .env, а откатываемся на него.
            if secret and not value:
                value = _env_defaults.get(key, "")
        else:
            value = _env_defaults.get(key, "")
        setattr(settings, attr, value)


def config_view(session: Session) -> list[dict]:
    """Текущие значения для страницы админки. Секреты — только маской и фактом
    «задан», никогда целиком."""
    from vivatlas.config import settings

    _capture_env_defaults()
    rows = []
    for key, (attr, secret) in _CONFIG_MAP.items():
        current = getattr(settings, attr) or ""
        rows.append(
            {
                "key": key,
                "attr": attr,
                "secret": secret,
                "value": security.mask_secret(current) if secret else current,
                "is_set": bool(current),
            }
        )
    return rows


def save_config(session: Session, values: dict[str, str | None]) -> None:
    """Сохранить правки конфигурации. Для секретов пустое поле = «оставить
    прежний» (как пароль SMTP); для несекретов пустое = очистить (вернуться к
    .env). После записи сразу накладываем на settings."""
    for key, (_attr, secret) in _CONFIG_MAP.items():
        if key not in values:
            continue
        value = (values[key] or "").strip()
        if secret:
            if value:
                set(session, key, security.encrypt_secret(value))
        else:
            set(session, key, value)
    apply_config_overrides(session)
