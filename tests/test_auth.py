import pytest

from vivatlas import auth, security, twofactor
from vivatlas.config import settings
from vivatlas.models import User


@pytest.fixture
def session(make_session):
    with make_session() as s:
        yield s


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "ключ-для-тестов-двери-длинный")


def make_user(session, email="boris@example.com", password="мама мыла раму синей", **kw):
    u = User(
        email=email,
        display_name="Boris",
        password_hash=security.hash_password(password),
        **kw,
    )
    session.add(u)
    session.flush()
    return u


# --- вход ---


def test_right_password_passes(session):
    make_user(session)
    r = auth.check_login(session, "boris@example.com", "мама мыла раму синей")
    assert r.ok and not r.needs_totp


def test_wrong_password_fails(session):
    make_user(session)
    r = auth.check_login(session, "boris@example.com", "не тот пароль совсем")
    assert not r.ok


def test_unknown_email_and_wrong_password_give_the_same_answer(session):
    # Ответ не должен выдавать, заведена почта или нет.
    make_user(session)
    a = auth.check_login(session, "boris@example.com", "неверный пароль тут")
    b = auth.check_login(session, "chужой@example.com", "неверный пароль тут")
    assert a.error == b.error
    assert not a.ok and not b.ok


def test_email_is_case_insensitive(session):
    make_user(session, email="boris@example.com")
    r = auth.check_login(session, "Boris@Example.COM", "мама мыла раму синей")
    assert r.ok


def test_lockout_after_too_many_tries(session):
    make_user(session)
    for _ in range(auth.MAX_FAILS):
        auth.check_login(session, "boris@example.com", "мимо")
    # Теперь заперт — даже верный пароль не пускает, пока не отлежится.
    r = auth.check_login(session, "boris@example.com", "мама мыла раму синей")
    assert not r.ok
    assert r.locked_minutes > 0


def test_good_login_resets_the_counter(session):
    u = make_user(session)
    for _ in range(3):
        auth.check_login(session, "boris@example.com", "мимо")
    assert u.failed_logins == 3
    auth.check_login(session, "boris@example.com", "мама мыла раму синей")
    assert u.failed_logins == 0


def test_inactive_user_cannot_enter(session):
    make_user(session, is_active=False)
    r = auth.check_login(session, "boris@example.com", "мама мыла раму синей")
    assert not r.ok


def test_two_factor_stops_at_second_step(session):
    from datetime import UTC, datetime

    make_user(session, totp_enabled_at=datetime.now(UTC))
    r = auth.check_login(session, "boris@example.com", "мама мыла раму синей")
    assert r.ok and r.needs_totp


# --- второй код ---


def test_totp_code_verifies(session):
    u = make_user(session)
    secret = twofactor.new_secret()
    u.totp_secret_enc = security.encrypt_secret(secret)
    import pyotp

    code = pyotp.TOTP(secret).now()
    assert twofactor.verify_totp(u, code)


def test_same_totp_code_is_not_accepted_twice(session):
    # Подсмотренный за плечом код живёт 30 секунд — второй раз не пускаем.
    u = make_user(session)
    secret = twofactor.new_secret()
    u.totp_secret_enc = security.encrypt_secret(secret)
    import pyotp

    code = pyotp.TOTP(secret).now()
    assert twofactor.verify_totp(u, code)
    assert not twofactor.verify_totp(u, code)


def test_wrong_totp_code_refused(session):
    u = make_user(session)
    u.totp_secret_enc = security.encrypt_secret(twofactor.new_secret())
    assert not twofactor.verify_totp(u, "000000")
    assert not twofactor.verify_totp(u, "буквы")


# --- коды восстановления ---


def test_backup_codes_are_issued_and_hashed(session):
    u = make_user(session)
    codes = twofactor.make_backup_codes(session, u)
    assert len(codes) == twofactor.BACKUP_CODES_COUNT
    # В базе только хеши — самих кодов там нет.
    for row in u.backup_codes:
        assert row.code_hash.startswith("$argon2id$")


def test_backup_code_works_once(session):
    u = make_user(session)
    codes = twofactor.make_backup_codes(session, u)
    assert twofactor.use_backup_code(session, u, codes[0])
    # Второй раз тот же код не годится.
    assert not twofactor.use_backup_code(session, u, codes[0])


def test_reissue_cancels_old_codes(session):
    u = make_user(session)
    old = twofactor.make_backup_codes(session, u)
    twofactor.make_backup_codes(session, u)  # перевыпуск
    assert not twofactor.use_backup_code(session, u, old[0])


def test_unused_count(session):
    u = make_user(session)
    codes = twofactor.make_backup_codes(session, u)
    twofactor.use_backup_code(session, u, codes[0])
    assert twofactor.unused_backup_count(u) == twofactor.BACKUP_CODES_COUNT - 1
