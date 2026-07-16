"""Настройки. Всё берётся из переменных окружения или файла .env."""

from collections.abc import Iterable
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

# Поля, которые нельзя показывать. Список явный, а не «всё, где есть слово
# token»: угадывание по имени однажды промахнётся молча.
_SECRET_FIELDS = frozenset(
    {
        "gitea_token",
        "github_token",
        "google_api_key",
        "secret_key",
    }
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    def __repr_args__(self) -> Iterable[tuple[str | None, Any]]:
        """Секреты не показываем даже в сообщении об ошибке.

        Не украшение. Настоящий случай: тест упал на строчке с настройками, и
        pydantic вывалил в текст ошибки токен Gitea и ключ Google целиком —
        в открытом виде, в терминал и в лог. Любое исключение, любая отладочная
        строчка, где мелькнёт объект настроек, утекает ключами.

        Правим здесь, а не переводом полей в «секретный тип»: тот тип всегда
        считается непустым, и девятнадцать проверок вида «если токен задан»
        тихо стали бы всегда истинными.
        """
        for name, value in super().__repr_args__():
            if name in _SECRET_FIELDS and value:
                yield name, "***скрыто***"
            else:
                yield name, value

    database_url: str = "sqlite:///./vivatlas.db"

    # Пусто по умолчанию, и это важно. Раньше здесь стоял адрес конкретного
    # сервера — и любая свежая установка пошла бы сканировать чужую Gitea,
    # просто потому что человек не заглянул в .env. Чужой адрес в значении по
    # умолчанию — это не мелочь, а чужой трафик и чужая нагрузка.
    gitea_url: str = ""
    gitea_token: str = ""

    # Нужен не для доступа — репозитории-источники открытые, — а чтобы GitHub
    # не резал по 60 запросов в час. Без токена тоже работает: на проверку
    # всех источников уходит 2-3 запроса.
    github_token: str = ""

    # Google AI Studio. Проверено на живых запросах 15.07.2026:
    #   pro (любой)          — "превышена квота", бесплатно недоступен;
    #   gemini-3.5-flash     — стабильный 503, бесплатно не отдают;
    #   gemini-2.5-flash     — 404, снят для новых пользователей;
    #   gemini-3.1-flash-lite — работает, ~0.8с на запрос.
    # Псевдонимы вида *-latest не берём: они молча переезжают на другую модель.
    google_api_key: str = ""
    llm_model: str = "gemini-3.1-flash-lite"
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 1536

    http_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 120.0

    # Бесплатный уровень ограничен запросами в минуту. Пауза между вызовами
    # дешевле, чем ловить 429 и переспрашивать.
    llm_delay_seconds: float = 1.0

    # Главный ключ двери. Из него выводится всё: подписи ссылок сброса пароля
    # и шифрование чужих токенов. Получить: vivatlas secret
    #
    # Сменить его — значит разлогинить всех и потерять сохранённые чужие
    # токены: их нечем будет расшифровать. Не потому что плохо сделано, а
    # потому что ключ и есть то, чем они держатся.
    secret_key: str = ""


settings = Settings()
