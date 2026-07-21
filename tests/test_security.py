import pytest

from vivatlas import security
from vivatlas.config import settings

# --- passwords ---


def test_password_is_never_stored_as_is():
    h = security.hash_password("correct horse battery staple")
    assert "horse" not in h
    assert h.startswith("$argon2id$")


def test_right_password_passes_wrong_does_not():
    h = security.hash_password("correct horse battery staple")
    assert security.verify_password("correct horse battery staple", h)
    assert not security.verify_password("correct horse battery stapl", h)
    assert not security.verify_password("", h)


def test_same_password_gives_different_hashes():
    # A unique salt for each. Otherwise identical passwords show up in the database as
    # identical strings, and whoever steals the database instantly knows who shares a password.
    a = security.hash_password("one and the same string here")
    b = security.hash_password("one and the same string here")
    assert a != b
    assert security.verify_password("one and the same string here", a)
    assert security.verify_password("one and the same string here", b)


def test_long_passphrase_is_not_cut_off():
    # The real reason argon2 is here and not bcrypt: bcrypt would silently
    # truncate the password at 72 bytes, and these two passwords would become one.
    base = "the long passphrase that a person came up with themselves and is really proud of"
    a = base + " ONE"
    b = base + " TWO"
    assert len(a.encode()) > 72
    h = security.hash_password(a)
    assert security.verify_password(a, h)
    assert not security.verify_password(b, h)


def test_broken_hash_is_a_no_not_a_crash():
    assert not security.verify_password("anything at all", "this is not a hash")
    assert not security.verify_password("anything at all", "")


# --- password strength check ---


def test_short_password_refused():
    # The function returns the reason KEY (translated at display time), not the text.
    assert security.check_password_strength("short") == "err.pw_short"


def test_common_password_refused():
    assert security.check_password_strength("password123")
    assert security.check_password_strength("qwertyuiop")
    assert security.check_password_strength("password")


def test_long_passphrase_accepted():
    assert security.check_password_strength("mother washed the frame with blue paint") == ""


def test_no_silly_rules():
    # An "uppercase, digit and asterisk" requirement drives people into Password1!.
    # A long phrase without a single digit is a good password, and we accept it.
    assert security.check_password_strength("it is raining in the yard today") == ""


# --- session keys ---


def test_tokens_are_unique_and_long():
    seen = {security.new_token() for _ in range(200)}
    assert len(seen) == 200
    assert all(len(t) >= 32 for t in seen)


def test_token_hash_is_stable_and_one_way():
    t = security.new_token()
    assert security.token_hash(t) == security.token_hash(t)
    assert t not in security.token_hash(t)
    assert len(security.token_hash(t)) == 64


def test_same_secret_compares_equal_and_not():
    assert security.same_secret("abc", "abc")
    assert not security.same_secret("abc", "abd")
    assert not security.same_secret("abc", "abcd")


# --- backup codes ---


def test_backup_code_is_readable_by_a_human():
    code = security.new_backup_code()
    assert len(code) == 14
    assert code.count("-") == 2
    # Hexadecimal alphabet: no O/0 or l/1 pairs, indistinguishable in handwriting.
    assert all(c in "0123456789abcdef-" for c in code)


def test_backup_codes_are_unique():
    assert len({security.new_backup_code() for _ in range(200)}) == 200


def test_backup_code_accepts_however_it_was_typed():
    # A person copies it off paper however it comes out. Rejecting over a space
    # is making them think the code is wrong.
    code = "4f7c-2a91-b3de"
    h = security.hash_backup_code(code)
    for typed in ("4f7c-2a91-b3de", "4F7C2A91B3DE", "4f7c 2a91 b3de", " 4f7c-2a91-b3de "):
        assert security.verify_backup_code(typed, h), typed


def test_wrong_backup_code_refused():
    h = security.hash_backup_code("4f7c-2a91-b3de")
    assert not security.verify_backup_code("4f7c-2a91-b3df", h)


def test_backup_code_is_hashed_not_stored():
    h = security.hash_backup_code("4f7c-2a91-b3de")
    assert "4f7c" not in h
    assert h.startswith("$argon2id$")


# --- encrypting third-party tokens ---


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "a-key-for-tests-that-is-long-and-random")


def test_token_encrypts_and_comes_back(secret):
    token = "gitea_abc123def456"
    blob = security.encrypt_secret(token)
    assert token not in blob
    assert security.decrypt_secret(blob) == token


def test_same_token_encrypts_differently_each_time(secret):
    # Otherwise identical strings in the database reveal that the token is the same.
    a = security.encrypt_secret("one and the same token")
    b = security.encrypt_secret("one and the same token")
    assert a != b
    assert security.decrypt_secret(a) == security.decrypt_secret(b)


def test_wrong_key_gives_nothing_not_a_crash(secret, monkeypatch):
    # A real case: SECRET_KEY was changed. The old tokens can never be read
    # again — and the right answer is "no token", not a crashed page.
    blob = security.encrypt_secret("token")
    monkeypatch.setattr(settings, "secret_key", "a completely different key")
    assert security.decrypt_secret(blob) == ""


def test_garbage_decrypts_to_nothing(secret):
    assert security.decrypt_secret("not ciphertext") == ""
    assert security.decrypt_secret("") == ""


def test_no_secret_key_is_an_honest_error(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    with pytest.raises(security.SecretMissing, match="SECRET_KEY"):
        security.encrypt_secret("token")


# --- showing a token ---


def test_masked_token_shows_ends_only():
    masked = security.mask_secret("gitea_abcdefghijklmnop")
    assert masked.startswith("gite")
    assert masked.endswith("mnop")
    assert "abcdefghijkl" not in masked
    assert "•" in masked


def test_short_token_hidden_completely():
    # For a short token, "the first four and last four" is the whole token.
    assert security.mask_secret("abcd1234") == "•" * 8


def test_empty_token_masks_to_empty():
    assert security.mask_secret("") == ""
