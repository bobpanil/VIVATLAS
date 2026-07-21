import pytest

from vivatlas import mailer
from vivatlas.runtime_settings import SmtpConfig

# --- building the message ---


def test_render_password_reset_carries_link():
    link = "https://x.example.com/reset?token=abc123"
    html, text = mailer.render("password_reset", link=link, name="Boris", minutes=60)
    assert link in html
    assert link in text
    assert "Boris" in html


def test_render_html_escapes_name():
    # A user-supplied name must not break the email's layout.
    html, _ = mailer.render(
        "password_reset", link="https://x/reset", name="<script>x</script>", minutes=60
    )
    assert "<script>x</script>" not in html
    assert "&lt;script&gt;" in html


def test_build_message_is_multipart_alternative():
    cfg = SmtpConfig(host="h", from_addr="from@example.com", from_name="VivAtlas")
    msg = mailer._build_message(cfg, "to@example.com", "Subject", "<b>hi</b>", "hi")
    assert msg["To"] == "to@example.com"
    assert msg["Subject"] == "Subject"
    assert "VivAtlas" in msg["From"]
    assert "from@example.com" in msg["From"]
    assert msg.is_multipart()
    types = {p.get_content_type() for p in msg.iter_parts()}
    assert "text/plain" in types
    assert "text/html" in types


# --- sending ---


async def test_send_unconfigured_raises():
    with pytest.raises(mailer.MailError):
        await mailer.send(SmtpConfig(), "to@x", "s", "<b>h</b>", "t")


async def test_send_ssl_params(monkeypatch):
    captured = {}

    async def fake_send(message, **kw):
        captured.update(kw)

    monkeypatch.setattr(mailer.aiosmtplib, "send", fake_send)
    cfg = SmtpConfig(
        host="smtp.x", port=465, security="ssl", username="u", password="p", from_addr="from@x"
    )
    await mailer.send(cfg, "to@x", "Subj", "<b>h</b>", "t")
    assert captured["hostname"] == "smtp.x"
    assert captured["port"] == 465
    assert captured["use_tls"] is True
    assert captured["start_tls"] is False
    assert captured["username"] == "u"
    assert captured["password"] == "p"


async def test_send_starttls_and_empty_auth(monkeypatch):
    captured = {}

    async def fake_send(message, **kw):
        captured.update(kw)

    monkeypatch.setattr(mailer.aiosmtplib, "send", fake_send)
    cfg = SmtpConfig(host="smtp.x", port=587, security="starttls", from_addr="from@x")
    await mailer.send(cfg, "to@x", "Subj", "<b>h</b>", "t")
    assert captured["start_tls"] is True
    assert captured["use_tls"] is False
    # Empty login/password go out as None — otherwise aiosmtplib would attempt AUTH.
    assert captured["username"] is None
    assert captured["password"] is None


async def test_send_wraps_smtp_error(monkeypatch):
    async def fake_send(message, **kw):
        raise mailer.aiosmtplib.SMTPException("host refused")

    monkeypatch.setattr(mailer.aiosmtplib, "send", fake_send)
    cfg = SmtpConfig(host="smtp.x", from_addr="from@x")
    with pytest.raises(mailer.MailError):
        await mailer.send(cfg, "to@x", "s", "<b>h</b>", "t")


async def test_send_crlf_in_recipient_becomes_mailerror(monkeypatch):
    # EmailMessage rejects a newline in the address (a barrier against header
    # injection) — but this must become a MailError, not fly off as a bare
    # ValueError from the background task. And it must not reach the actual send.
    sent = {"called": False}

    async def fake_send(message, **kw):
        sent["called"] = True

    monkeypatch.setattr(mailer.aiosmtplib, "send", fake_send)
    cfg = SmtpConfig(host="smtp.x", from_addr="from@x")
    with pytest.raises(mailer.MailError):
        await mailer.send(cfg, "victim@x\nBcc: evil@x", "subject", "<b>h</b>", "t")
    assert sent["called"] is False
