"""Sending mail and assembling messages.

The single place in the whole program where we talk to the mail server. Settings
come from the panel (runtime_settings), not from .env: the owner edits them on the fly.

Asynchronous (aiosmtplib) so that sending doesn't tie up the server: a mail host is
sometimes slow, and we mustn't drop the page because of it. We wrap the error in MailError
with clear text — we show the user the reason, not an SMTP stack trace.
"""

import logging
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

from vivatlas import i18n
from vivatlas.runtime_settings import SmtpConfig

log = logging.getLogger(__name__)

# We wait no longer than this for the mail host to reply. A slow host is common;
# hanging on it for minutes is not an option — better to honestly say "not sent".
_TIMEOUT_SECONDS = 30

_EMAIL_DIR = Path(__file__).parent / "templates" / "email"

# A small dedicated template engine for emails: emails have their own directory and
# their own escaping. We auto-escape HTML (someone else's name in an email must not
# break the layout), but not .txt — there's no markup there.
_env = Environment(
    loader=FileSystemLoader(str(_EMAIL_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
)


class MailError(RuntimeError):
    """The email didn't go out. Text for the user, reason for the log."""


def render(name: str, lang: str = "en", /, **ctx) -> tuple[str, str]:
    """Assemble an email: (html, plaintext) from the pair of templates name.html and name.txt.

    Emails have their own Jinja engine (a separate directory), so t/lang/dir, which the
    context_processor puts into web templates, are passed here by hand — in the language
    of the recipient (whoever requested the email)."""
    base = {
        "t": lambda key, **kw: i18n.translate(key, lang, **kw),
        "lang": lang,
        "dir": i18n.dir_for(lang),
        **ctx,
    }
    html = _env.get_template(f"{name}.html").render(**base)
    text = _env.get_template(f"{name}.txt").render(**base)
    return html, text


def _build_message(cfg: SmtpConfig, to: str, subject: str, html: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.from_name, cfg.effective_from))
    msg["To"] = to
    msg["Subject"] = subject
    # Text first, then html: a recipient without html mail sees the first,
    # with html — the second. The order matters for multipart/alternative.
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


async def send(cfg: SmtpConfig, to: str, subject: str, html: str, text: str) -> None:
    """Send an email. Not configured or it failed — MailError with the reason."""
    if not cfg.is_configured:
        raise MailError(
            "Mail is not configured: enter the SMTP host and return address in the panel."
        )

    # none — no encryption; starttls — a regular port that upgrades to TLS;
    # ssl — TLS from the first byte (usually port 465). Explicit True/False, not
    # "however it turns out": a silent fallback to unencrypted is a password leak.
    start_tls = cfg.security == "starttls"
    use_tls = cfg.security == "ssl"

    try:
        # Assembling the message is inside try too: EmailMessage raises ValueError if
        # a line break slipped into the address or subject (which is itself a guard against
        # header injection). Such a failure should become a MailError, not fly out as
        # an unhandled exception from a background task.
        msg = _build_message(cfg, to, subject, html, text)
        await aiosmtplib.send(
            msg,
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username or None,
            password=cfg.password or None,
            start_tls=start_tls,
            use_tls=use_tls,
            timeout=_TIMEOUT_SECONDS,
        )
    except (aiosmtplib.SMTPException, OSError, ValueError) as exc:
        # OSError — host unreachable/timeout; SMTPException — server rejection
        # (login, address); ValueError — a line break in the address/subject or a bad
        # port. Reason to the log, a short string to the user.
        log.warning("mail not sent to %s: %s", to, exc)
        raise MailError(f"Failed to send email: {exc}") from exc
