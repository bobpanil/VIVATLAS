"""Two-step verification: a code from an app (TOTP) and backup codes.

TOTP is the same mechanism as in Google Authenticator: the app and the server
share a secret and, by the clock, compute a six-digit code that changes every
30 seconds. A password can be glimpsed once and reused; a code lives half a minute.

The secret is stored encrypted: it is equivalent to a second password, and in
plaintext it would defeat the whole point. It is decrypted only to check a code.
"""

import pyotp
import qrcode
import qrcode.image.svg
from sqlalchemy.orm import Session

from vivatlas import security
from vivatlas.models import BackupCode, User

# How many backup codes we issue. Ten is enough: this is for a lost phone,
# not a daily sign-in.
BACKUP_CODES_COUNT = 10

_ISSUER = "VivAtlas"


def new_secret() -> str:
    """A random secret for linking the app."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """The otpauth:// string that the app reads from the QR code."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_ISSUER)


def qr_svg(uri: str) -> str:
    """QR as SVG — drawn without images, hence without Pillow."""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


def verify_totp(user: User, code: str, secret: str | None = None) -> bool:
    """Whether the code is valid. We accept one and the same code only once.

    A ±1 window: if the phone's and server's clocks have drifted a little, the
    neighbouring code also works. Without this, people with drifting clocks
    would never sign in.

    Replay protection: a code peeked over the shoulder lives 30 seconds, and in
    that time it could be entered a second time. We remember the last accepted
    code and don't let the same code through twice.
    """
    code = code.strip().replace(" ", "")
    if not code.isdigit():
        return False
    enc = secret if secret is not None else security.decrypt_secret(user.totp_secret_enc)
    if not enc:
        return False
    if not pyotp.TOTP(enc).verify(code, valid_window=1):
        return False
    if user.totp_last_code == code:
        return False  # this code has already been used
    user.totp_last_code = code
    return True


def make_backup_codes(session: Session, user: User) -> list[str]:
    """Issue a new set of codes. We wipe the old ones: reissuing cancels the previous.

    Returns the codes in plaintext — they are shown to the user ONLY ONCE.
    Only hashes go into the database.
    """
    for old in list(user.backup_codes):
        user.backup_codes.remove(old)
        session.delete(old)
    session.flush()

    codes = [security.new_backup_code() for _ in range(BACKUP_CODES_COUNT)]
    for code in codes:
        # Via the relationship, not session.add: otherwise the in-memory
        # collection would stay empty until a reload, and there would be
        # nothing to redeem a fresh code against. Caught by a test.
        user.backup_codes.append(BackupCode(code_hash=security.hash_backup_code(code)))
    session.flush()
    return codes


def use_backup_code(session: Session, user: User, code: str) -> bool:
    """Redeem a backup code. Single-use: a used one is no longer valid."""
    for row in user.backup_codes:
        if row.used_at is None and security.verify_backup_code(code, row.code_hash):
            from datetime import UTC, datetime

            row.used_at = datetime.now(UTC)
            return True
    return False


def unused_backup_count(user: User) -> int:
    return sum(1 for c in user.backup_codes if c.used_at is None)
