"""Отправка почты и сборка писем.

Одно место на всю программу, где мы говорим с почтовым сервером. Настройки
берутся из панели (runtime_settings), не из .env: хозяин правит их на ходу.

Асинхронно (aiosmtplib), чтобы отправка не держала сервер: почтовый узел бывает
медленным, а ронять из-за него страницу нельзя. Ошибку заворачиваем в MailError
с понятным текстом — наружу человеку показываем причину, а не стектрейс SMTP.
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

# Ждём ответа почтового узла не дольше этого. Медленный узел — обычное дело;
# висеть на нём минутами нельзя, лучше честно сказать «не отправилось».
_TIMEOUT_SECONDS = 30

_EMAIL_DIR = Path(__file__).parent / "templates" / "email"

# Свой маленький движок шаблонов для писем: у писем свой каталог и своё
# экранирование. Автоэкранируем HTML (чужое имя в письме не должно ломать
# вёрстку), но не .txt — там разметки нет.
_env = Environment(
    loader=FileSystemLoader(str(_EMAIL_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html",), default_for_string=False),
)


class MailError(RuntimeError):
    """Письмо не ушло. Текст — человеку, причину — в журнал."""


def render(name: str, lang: str = "en", /, **ctx) -> tuple[str, str]:
    """Собрать письмо: (html, plaintext) из пары шаблонов name.html и name.txt.

    У писем свой движок Jinja (отдельный каталог), поэтому t/lang/dir, которые в
    веб-шаблоны кладёт context_processor, здесь передаём руками — на язык
    получателя (кто запросил письмо)."""
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
    # Сначала текст, потом html: получатель без html-почты увидит первый,
    # с html — второй. Порядок для multipart/alternative значим.
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


async def send(cfg: SmtpConfig, to: str, subject: str, html: str, text: str) -> None:
    """Отправить письмо. Не настроено или сорвалось — MailError с причиной."""
    if not cfg.is_configured:
        raise MailError("Почта не настроена: впишите узел SMTP и обратный адрес в панели.")

    # none — без шифрования; starttls — обычный порт с переходом на TLS;
    # ssl — TLS с первого байта (обычно порт 465). Явные True/False, а не
    # «как получится»: молчаливый откат на незашифрованное — это утечка пароля.
    start_tls = cfg.security == "starttls"
    use_tls = cfg.security == "ssl"

    try:
        # Сборка письма — тоже внутри try: EmailMessage бросает ValueError, если
        # в адресе или теме затесался перевод строки (сам по себе это заслон от
        # инъекции заголовков). Такой отказ должен стать MailError, а не улететь
        # необработанным исключением из фоновой задачи.
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
        # OSError — узел недоступен/таймаут; SMTPException — отказ сервера
        # (логин, адрес); ValueError — перевод строки в адресе/теме или кривой
        # порт. Причину в журнал, человеку — короткую строку.
        log.warning("почта не отправлена на %s: %s", to, exc)
        raise MailError(f"Не удалось отправить письмо: {exc}") from exc
