import pytest

from skill_atlas import security
from skill_atlas.config import settings

# --- пароли ---


def test_password_is_never_stored_as_is():
    h = security.hash_password("правильная лошадь батарейка скрепка")
    assert "лошадь" not in h
    assert h.startswith("$argon2id$")


def test_right_password_passes_wrong_does_not():
    h = security.hash_password("правильная лошадь батарейка скрепка")
    assert security.verify_password("правильная лошадь батарейка скрепка", h)
    assert not security.verify_password("правильная лошадь батарейка скрепк", h)
    assert not security.verify_password("", h)


def test_same_password_gives_different_hashes():
    # Соль своя у каждого. Иначе одинаковые пароли видны в базе как
    # одинаковые строки, и укравший базу сразу знает, у кого пароль общий.
    a = security.hash_password("одна и та же строка тут")
    b = security.hash_password("одна и та же строка тут")
    assert a != b
    assert security.verify_password("одна и та же строка тут", a)
    assert security.verify_password("одна и та же строка тут", b)


def test_long_passphrase_is_not_cut_off():
    # Настоящая причина, по которой тут argon2, а не bcrypt: bcrypt молча
    # обрезал бы пароль на 72 байте, и эти два пароля стали бы одним.
    base = "длинная парольная фраза которую человек придумал сам и гордится ею"
    a = base + " ОДИН"
    b = base + " ДВА"
    assert len(a.encode()) > 72
    h = security.hash_password(a)
    assert security.verify_password(a, h)
    assert not security.verify_password(b, h)


def test_broken_hash_is_a_no_not_a_crash():
    assert not security.verify_password("что угодно", "это не хеш")
    assert not security.verify_password("что угодно", "")


# --- проверка пароля на прочность ---


def test_short_password_refused():
    assert "12 знаков" in security.check_password_strength("коротко")


def test_common_password_refused():
    assert security.check_password_strength("password123")
    assert security.check_password_strength("qwertyuiop")
    assert security.check_password_strength("пароль")


def test_long_passphrase_accepted():
    assert security.check_password_strength("мама мыла раму синей краской") == ""


def test_no_silly_rules():
    # Требование «заглавная, цифра и звёздочка» выгоняет людей в Password1!.
    # Длинная фраза без единой цифры — хороший пароль, и мы его принимаем.
    assert security.check_password_strength("сегодня во дворе идёт дождь") == ""


# --- ключи сессий ---


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


# --- коды восстановления ---


def test_backup_code_is_readable_by_a_human():
    code = security.new_backup_code()
    assert len(code) == 14
    assert code.count("-") == 2
    # Шестнадцатеричный алфавит: нет пар O/0 и l/1, неразличимых в рукописи.
    assert all(c in "0123456789abcdef-" for c in code)


def test_backup_codes_are_unique():
    assert len({security.new_backup_code() for _ in range(200)}) == 200


def test_backup_code_accepts_however_it_was_typed():
    # Человек перепишет с бумаги как получится. Отказать из-за пробела —
    # заставить его думать, что код не тот.
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


# --- шифрование чужих токенов ---


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "ключ-для-тестов-он-длинный-и-случайный")


def test_token_encrypts_and_comes_back(secret):
    token = "gitea_abc123def456"
    blob = security.encrypt_secret(token)
    assert token not in blob
    assert security.decrypt_secret(blob) == token


def test_same_token_encrypts_differently_each_time(secret):
    # Иначе по одинаковым строкам в базе видно, что токен один и тот же.
    a = security.encrypt_secret("один и тот же токен")
    b = security.encrypt_secret("один и тот же токен")
    assert a != b
    assert security.decrypt_secret(a) == security.decrypt_secret(b)


def test_wrong_key_gives_nothing_not_a_crash(secret, monkeypatch):
    # Настоящий случай: сменили SECRET_KEY. Старые токены не прочитать
    # никогда — и правильный ответ «токена нет», а не падение страницы.
    blob = security.encrypt_secret("токен")
    monkeypatch.setattr(settings, "secret_key", "совсем другой ключ")
    assert security.decrypt_secret(blob) == ""


def test_garbage_decrypts_to_nothing(secret):
    assert security.decrypt_secret("не шифртекст") == ""
    assert security.decrypt_secret("") == ""


def test_no_secret_key_is_an_honest_error(monkeypatch):
    monkeypatch.setattr(settings, "secret_key", "")
    with pytest.raises(security.SecretMissing, match="SECRET_KEY"):
        security.encrypt_secret("токен")


# --- показ токена ---


def test_masked_token_shows_ends_only():
    masked = security.mask_secret("gitea_abcdefghijklmnop")
    assert masked.startswith("gite")
    assert masked.endswith("mnop")
    assert "abcdefghijkl" not in masked
    assert "•" in masked


def test_short_token_hidden_completely():
    # У короткого токена «первые четыре и последние четыре» — это весь токен.
    assert security.mask_secret("abcd1234") == "•" * 8


def test_empty_token_masks_to_empty():
    assert security.mask_secret("") == ""
