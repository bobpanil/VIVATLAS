"""Hashes, keys, encryption. One place for the whole program.

This gathers everything where it's easy to make a silent mistake. The rules
followed here are worth knowing:

  - the password is never stored anywhere. Only the argon2id hash. We ourselves
    can't recover a user's password — and that's how it should be;
  - argon2id, not bcrypt: bcrypt silently truncates the password at 72 bytes. A
    long passphrase becomes just its first 72 bytes with it, and the user never
    finds out;
  - comparing secrets — only in constant time. A plain == breaks out of the
    loop on the first mismatched byte, and the response timing lets you guess a
    secret character by character;
  - the database holds hashes of session keys, not the keys themselves. Steal
    the database and you don't get ready-made passes;
  - other people's tokens (Gitea, GitHub) are encrypted. They aren't ours, and
    losing them to our own mistake isn't acceptable.
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
    """No secret key. Without it the door won't lock."""


# argon2id settings. The values aren't arbitrary: this is the OWASP 2024
# recommendation — 19 MB of memory, 2 passes. Memory is the key here: it makes
# brute-forcing on GPUs expensive, and GPUs are exactly what's used for it.
_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19 * 1024,
    parallelism=1,
)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    """Whether the password matches. No exceptions escape — only yes or no.

    The UnicodeEncodeError here isn't for show. A corrupted hash with Cyrillic
    inside makes argon2 crash when it tries to coerce it to ascii — and that's
    not a "server error", it's simply "that hash doesn't match". Covered by a test.
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
    """The hash was computed with old settings — worth recomputing at sign-in."""
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return False


def check_password_strength(password: str) -> str:
    """Empty string — it's fine. Otherwise a reason KEY (translated at the point
    of display): business logic shouldn't know the interface language.

    The rules are deliberately few. Requirements like "uppercase, digit and
    asterisk" push people into Password1! — short and predictable. Length
    decides more, so we ask only about it.
    """
    if len(password) < 12:
        return "err.pw_short"
    if len(password.encode("utf-8")) > 1024:
        return "err.pw_long"
    lowered = password.lower().strip()
    if lowered in _COMMON:
        return "err.pw_common"
    return ""


# Not a million-entry list, just the ones tried first. A full check against
# breach data is a separate task and a separate dependency.
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
    "qwertyuiop",
    "1qaz2wsx",
    "zaq12wsx",
    "changeme",
    "secret",
    "passw0rd",
    "p@ssw0rd",
    "skillatlas",
}


# --- session and invitation keys -------------------------------------------


def new_token(nbytes: int = 32) -> str:
    """A random key. secrets, not random: random is predictable by its very nature."""
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """Hash of the key for the database.

    sha256 without salt here — and that's not an oversight. Salt is needed
    against dictionaries, but a session key is 32 random bytes: there's no
    dictionary for them. On the other hand, an unsalted hash can be looked up
    in the database with a single indexed query.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def same_secret(a: str, b: str) -> bool:
    """Compare secrets in constant time."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


# --- backup codes ---------------------------------------------------------


def new_backup_code() -> str:
    """A code for when the phone is lost.

    Shaped like "4f7c-2a91-b3de": in groups, so the user can copy it onto paper
    and not lose their mind. The alphabet is hexadecimal — it has no pairs like
    O and 0 that are impossible to tell apart in handwriting.
    """
    raw = secrets.token_hex(6)
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:12]}"


def normalize_backup_code(code: str) -> str:
    """The user will enter it however they like: with spaces, no dashes, uppercase."""
    return "".join(ch for ch in code.lower() if ch.isalnum())


def hash_backup_code(code: str) -> str:
    """Backup codes are just passwords, so argon2.

    There are only 10 of them and they're short: sha256 over them is brute-forced
    in seconds.
    """
    return _hasher.hash(normalize_backup_code(code))


def verify_backup_code(code: str, stored_hash: str) -> bool:
    return verify_password(normalize_backup_code(code), stored_hash)


# --- encryption of other people's tokens -----------------------------------


def _fernet() -> Fernet:
    """The encryption key is derived from the secret key, not stored separately.

    That way the user has one secret in .env instead of two, and no temptation
    to put the second one next to the database. HKDF with a label: if tomorrow
    we need to encrypt something else, the label yields a different key from the
    same secret.
    """
    if not settings.secret_key:
        raise SecretMissing(
            "SECRET_KEY is not set. Get one: vivatlas secret — and put it in .env.\n"
            "Without it you can neither lock the door nor encrypt others' tokens."
        )
    key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"skill-atlas/token-encryption/v1",
    ).derive(settings.secret_key.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain: str) -> str:
    """Encrypt someone else's token for the database."""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(blob: str) -> str:
    """Decrypt. Didn't work out — empty, not an exception upward.

    It usually fails for one reason: SECRET_KEY was changed. Then the old tokens
    can never be read again, and the right answer is "no token, enter it again",
    not crashing the whole page.
    """
    if not blob:
        return ""
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""


def mask_secret(plain: str) -> str:
    """How to show a token without showing it.

    The first and last characters are there so the user can recognize their
    token among several. The middle is never shown — not on the page, not in the
    server response.
    """
    if not plain:
        return ""
    if len(plain) <= 8:
        return "•" * len(plain)
    return f"{plain[:4]}{'•' * 8}{plain[-4:]}"


def require_secret() -> None:
    """Check the secret key. Called at startup to learn of trouble ahead of time,
    not at the moment the user clicks "Sign in"."""
    _fernet()
