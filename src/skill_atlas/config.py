"""Настройки. Всё берётся из переменных окружения или файла .env."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite:///./skill_atlas.db"

    gitea_url: str = "https://git.example.com"
    gitea_token: str = ""

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


settings = Settings()
