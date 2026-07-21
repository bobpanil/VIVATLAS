"""Settings the owner changes on the fly — layered on top of the settings table.

Not in .env: .env is read once at startup, whereas these (email, site URL,
whether registration is open) the owner edits from the panel, and the edit must
survive a restart without needing access to a file on the server.

The SMTP password is someone else's secret, like Gitea tokens: it lives in the
database encrypted (Fernet on the secret key) and leaves only masked. It is
never stored in plaintext anywhere.
"""

from dataclasses import dataclass

from sqlalchemy.orm import Session

from vivatlas import security
from vivatlas.models import Setting

# Keys in the settings table. Gathered here so string literals aren't scattered
# across the code: a typo in a key is a silently lost setting.
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


# --- raw access to the key/value pair --------------------------------------


def get(session: Session, key: str, default: str = "") -> str:
    row = session.get(Setting, key)
    return row.value if row is not None else default


def set(session: Session, key: str, value: str) -> None:
    """Write a value. Creates the row if it doesn't exist yet (upsert)."""
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


# --- site URL (for links in emails) ----------------------------------------


def site_url(session: Session) -> str:
    """The address used to build links in emails (password reset, etc.).

    Behind a tunnel the real external address isn't visible to the program from
    the inside, and the http address from the request points at the server, not
    the domain. So the owner sets the address; if it's unset, the caller falls
    back to the address from the request.
    """
    return get(session, SITE_URL, "").strip().rstrip("/")


# --- whether registration is open ------------------------------------------
#
# Open by default. The owner can close it from the panel — then only by
# invitation or by creating accounts from the panel. Read here so that both the
# registration page and its link ask the same place.


def registration_open(session: Session) -> bool:
    return get_bool(session, REGISTRATION_OPEN, default=True)


# --- SMTP ------------------------------------------------------------------


@dataclass(frozen=True)
class SmtpConfig:
    host: str = ""
    port: int = 587
    security: str = "starttls"  # none | starttls | ssl
    username: str = ""
    password: str = ""  # decrypted; only ever exposed through mask
    from_addr: str = ""
    from_name: str = "VivAtlas"

    @property
    def is_configured(self) -> bool:
        """Whether there's enough to even attempt sending.

        Host and return address are enough. We don't require login/password:
        there are internal relays without sign-in. A missing host isn't an
        "error" but "email isn't set up yet", and there's simply no point
        calling send then.
        """
        return bool(self.host and self.effective_from)

    @property
    def effective_from(self) -> str:
        """Return address. Not set explicitly — use the login, which is usually the address."""
        return (self.from_addr or self.username).strip()


def get_smtp(session: Session) -> SmtpConfig:
    """Assemble the email settings from the database. The password is decrypted here."""
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
    """Save the email settings.

    We touch the password ONLY if a non-empty string is passed: an empty field
    in the form means "keep the existing one", not "wipe it" — otherwise every
    save of other fields would silently blank the password. Exactly like with
    one's own source tokens in settings.
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
    """Masked SMTP password — only so the user can see that it's set."""
    plain = security.decrypt_secret(get(session, SMTP_PASSWORD_ENC, ""))
    return security.mask_secret(plain)


def _clean_security(mode: str) -> str:
    mode = (mode or "").strip().lower()
    return mode if mode in _SECURITY_MODES else "starttls"


# --- operational configuration on top of .env ------------------------------
#
# Keys from .env that the owner edits from the admin panel. We store the
# overrides in the settings table (secrets encrypted) and OVERLAY them onto the
# global settings object at startup and after every save. That way all the code
# reading settings.gitea_token etc. picks up the edit without a restart and
# without touching the consumers themselves. Empty in the database — we return
# the value from .env.
#
# SECRET_KEY and DATABASE_URL are deliberately NOT included here: all decryption
# and the connection itself rest on them — changing them on the fly from the UI
# is dangerous.

CFG_GITEA_URL = "cfg_gitea_url"
CFG_GITEA_TOKEN = "cfg_gitea_token"  # secret
CFG_GITHUB_TOKEN = "cfg_github_token"  # secret
CFG_GOOGLE_KEY = "cfg_google_api_key"  # secret
CFG_LLM_MODEL = "cfg_llm_model"
CFG_EMBEDDING_MODEL = "cfg_embedding_model"

# setting key -> (settings attribute, whether secret)
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
    """Remember the values from .env ONCE — before the first overlay of edits.
    Needed so that clearing an edit returns to .env, not to emptiness."""
    if _env_defaults:
        return
    from vivatlas.config import settings

    for key, (attr, _secret) in _CONFIG_MAP.items():
        _env_defaults[key] = getattr(settings, attr) or ""


def apply_config_overrides(session: Session) -> None:
    """Overlay the edits from the database onto the global settings. Called at
    startup and after saving in the admin panel."""
    from vivatlas.config import settings

    _capture_env_defaults()
    for key, (attr, secret) in _CONFIG_MAP.items():
        raw = get(session, key, "")
        if raw:
            value = security.decrypt_secret(raw) if secret else raw
            # Secret didn't decrypt (SECRET_KEY was changed) — don't overwrite
            # the working value from .env with empty, roll back to it instead.
            if secret and not value:
                value = _env_defaults.get(key, "")
        else:
            value = _env_defaults.get(key, "")
        setattr(settings, attr, value)


def config_view(session: Session) -> list[dict]:
    """Current values for the admin page. Secrets — only as a mask and the fact
    that they're "set", never in full."""
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
    """Save configuration edits. For secrets an empty field = "keep the existing
    one" (like the SMTP password); for non-secrets empty = clear (revert to
    .env). After writing we immediately overlay onto settings."""
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
