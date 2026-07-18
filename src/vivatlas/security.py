"""Хеши, ключи, шифрование. Одно место на всю программу.

Здесь собрано всё, где легко ошибиться незаметно. Правила, которые тут
соблюдаются, стоит знать:

  - пароль не хранится нигде. Только хеш argon2id. Мы сами не можем узнать
    пароль пользователя — и это правильно;
  - argon2id, а не bcrypt: bcrypt молча обрезает пароль на 72 байте. Длинная
    парольная фраза с ним превращается в свои первые 72 байта, и человек об
    этом не узнаёт;
  - сравнение секретов — только постоянное по времени. Обычное == выходит из
    цикла на первом несовпавшем байте, и по времени ответа можно подбирать
    секрет посимвольно;
  - в базе лежат хеши ключей сессий, а не сами ключи. Украдут базу — не
    получат готовых пропусков;
  - чужие токены (Gitea, GitHub) шифруются. Их у нас не свои, и терять их
    чужой ошибкой нельзя.
"""

import base64
import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from vivatlas.config import settings


class SecretMissing(RuntimeError):
    """Нет главного ключа. Без него дверь не запирается."""


# Настройки argon2id. Значения не с потолка: это рекомендация OWASP на 2024 —
# 19 МБ памяти, 2 прохода. Память тут главное: она делает перебор на видеокартах
# дорогим, а именно ими и перебирают.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Подходит ли пароль. Никаких исключений наружу — только да или нет.

    UnicodeEncodeError тут не для красоты. Испорченный хеш с кириллицей внутри
    заставляет argon2 падать при попытке привести его к ascii — а это не
    «ошибка сервера», это просто «такой хеш нам не подходит». Поймано тестом.
    """
    try:
        _hasher.verify(stored_hash, password)
        return True
    except (
        VerifyMismatchError,
        VerificationError,
        InvalidHashError,
        UnicodeEncodeError,
        TypeError,
    ):
        return False


def password_needs_rehash(stored_hash: str) -> bool:
    """Хеш посчитан по старым настройкам — стоит пересчитать при входе."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def check_password_strength(password: str) -> str:
    """Пустая строка — годится. Иначе КЛЮЧ причины (переводится на месте показа):
    бизнес-логика не должна знать язык интерфейса.

    Правил намеренно мало. Требования вида «заглавная, цифра и звёздочка»
    выгоняют людей в Password1! — короткий и предсказуемый. Длина решает
    больше, поэтому спрашиваем только её.
    """
    if len(password) < 12:
        return "err.pw_short"
    if len(password.encode("utf-8")) > 1024:
        return "err.pw_long"
    lowered = password.lower().strip()
    if lowered in _COMMON:
        return "err.pw_common"
    return ""


# Не список на миллион, а те, что подбирают первыми. Полноценная проверка по
# утечкам — отдельная задача и отдельная зависимость.
_COMMON = {
    "password",
    "password1",
    "password123",
    "qwerty",
    "qwerty123",
    "123456",
    "1234567890",
    "12345678",
    "111111",
    "000000",
    "iloveyou",
    "admin",
    "administrator",
    "letmein",
    "welcome",
    "monkey",
    "dragon",
    "sunshine",
    "princess",
    "football",
    "пароль",
    "йцукен",
    "qwertyuiop",
    "1qaz2wsx",
    "zaq12wsx",
    "changeme",
    "secret",
    "passw0rd",
    "p@ssw0rd",
    "skillatlas",
}


# --- ключи сессий и приглашений -------------------------------------------


def new_token(nbytes: int = 32) -> str:
    """Случайный ключ. secrets, а не random: random предсказуем по своей сути."""
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Хеш ключа для базы.

    Здесь sha256 без соли — и это не оплошность. Соль нужна против словарей,
    а ключ сессии это 32 случайных байта: словаря на них не бывает. Зато
    хеш без соли можно искать в базе одним запросом по индексу.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same_secret(a: str, b: str) -> bool:
    """Сравнение секретов за постоянное время."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --- коды восстановления ---------------------------------------------------


def new_backup_code() -> str:
    """Код на случай потери телефона.

    Вид «4f7c-2a91-b3de»: группами, чтобы человек мог переписать на бумагу и
    не сойти с ума. Алфавит шестнадцатеричный — в нём нет пар вроде O и 0,
    которые невозможно различить в рукописи.
    """
    raw = secrets.token_hex(6)
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def normalize_backup_code(code: str) -> str:
    """Человек введёт как получится: с пробелами, без дефисов, заглавными."""
    return "".join(ch for ch in code.lower() if ch.isalnum())


def hash_backup_code(code: str) -> str:
    """Коды восстановления — те же пароли, значит argon2.

    Их всего 10 и они короткие: sha256 по ним перебирается за секунды.
    """
    return _hasher.hash(normalize_backup_code(code))


def verify_backup_code(code: str, stored_hash: str) -> bool:
    return verify_password(normalize_backup_code(code), stored_hash)


# --- шифрование чужих токенов ----------------------------------------------


def _fernet() -> Fernet:
    """Ключ шифрования выводится из главного ключа, а не хранится отдельно.

    Так у человека один секрет в .env вместо двух, и нет соблазна положить
    второй рядом с базой. HKDF с меткой: если завтра понадобится шифровать
    что-то ещё, метка даст другой ключ из того же секрета.
    """
    if not settings.secret_key:
        raise SecretMissing(
            "Не задан SECRET_KEY. Получить: vivatlas secret — и вписать в .env.\n"
            "Без него нельзя ни запереть дверь, ни зашифровать чужие токены."
        )
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"skill-atlas/token-encryption/v1",
    ).derive(settings.secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    """Зашифровать чужой токен для базы."""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    """Расшифровать. Не поддалось — пусто, а не исключение наверх.

    Не поддаётся обычно по одной причине: сменили SECRET_KEY. Тогда старые
    токены не прочитать никогда, и правильный ответ — «токена нет, впишите
    заново», а не падение всей страницы.
    """
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask_secret(plain: str) -> str:
    """Как показать токен, не показав его.

    Первые и последние знаки нужны, чтобы человек узнал свой токен среди
    нескольких. Середина не показывается никогда — ни на странице, ни в ответе
    сервера.
    """
    if not plain:
        return ""
    if len(plain) <= 8:
        return "•" * len(plain)
    return f"{plain[:4]}{'•' * 8}{plain[-4:]}"


def require_secret() -> None:
    """Проверить главный ключ. Зовётся при запуске, чтобы узнать о беде
    заранее, а не в момент, когда человек жмёт «Войти»."""
    _fernet()
