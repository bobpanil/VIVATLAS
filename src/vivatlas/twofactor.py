"""Двухэтажная проверка: код из приложения (TOTP) и коды восстановления.

TOTP — тот же механизм, что в Google Authenticator: приложение и сервер знают
общий секрет и по часам считают шестизначный код, меняющийся каждые 30 секунд.
Пароль можно подсмотреть один раз и пользоваться; код живёт полминуты.

Секрет хранится зашифрованным: он равносилен второму паролю, и в открытом виде
обесценил бы всю затею. Расшифровывается только чтобы сверить код.
"""

import pyotp
import qrcode
import qrcode.image.svg
from sqlalchemy.orm import Session

from vivatlas import security
from vivatlas.models import BackupCode, User

# Сколько кодов восстановления выдаём. Десяти хватает: это на случай потери
# телефона, а не ежедневный вход.
BACKUP_CODES_COUNT = 10

_ISSUER = "VivAtlas"


def new_secret() -> str:
    """Случайный секрет для привязки приложения."""
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    """Строка otpauth://, которую приложение читает с QR-кода."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_ISSUER)


def qr_svg(uri: str) -> str:
    """QR как SVG — рисуем без картинок, значит без Pillow."""
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    return img.to_string(encoding="unicode")


def verify_totp(user: User, code: str, secret: str | None = None) -> bool:
    """Верен ли код. Один и тот же код принимаем только раз.

    Окно ±1: если часы телефона и сервера чуть разошлись, соседний код тоже
    подходит. Без этого люди с плывущими часами не войдут никогда.

    Защита от повтора: подсмотренный за плечом код живёт 30 секунд, и за это
    время его можно ввести второй раз. Запоминаем последний принятый и второй
    раз тот же код не пускаем.
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
        return False  # этот код уже использован
    user.totp_last_code = code
    return True


def make_backup_codes(session: Session, user: User) -> list[str]:
    """Выдать новый набор кодов. Старые стираем: перевыпуск отменяет прежние.

    Возвращает коды в открытом виде — их показывают человеку ЕДИНСТВЕННЫЙ раз.
    В базу ложатся только хеши.
    """
    for old in list(user.backup_codes):
        user.backup_codes.remove(old)
        session.delete(old)
    session.flush()

    codes = [security.new_backup_code() for _ in range(BACKUP_CODES_COUNT)]
    for code in codes:
        # Через relationship, а не session.add: иначе коллекция в памяти
        # осталась бы пустой до перезагрузки, и погасить свежий код было бы
        # нечем. Поймано тестом.
        user.backup_codes.append(BackupCode(code_hash=security.hash_backup_code(code)))
    session.flush()
    return codes


def use_backup_code(session: Session, user: User, code: str) -> bool:
    """Погасить код восстановления. Одноразовый: использованный больше не годен."""
    for row in user.backup_codes:
        if row.used_at is None and security.verify_backup_code(code, row.code_hash):
            from datetime import UTC, datetime

            row.used_at = datetime.now(UTC)
            return True
    return False


def unused_backup_count(user: User) -> int:
    return sum(1 for c in user.backup_codes if c.used_at is None)
