"""Settings. Everything comes from environment variables or the .env file."""

from collections.abc import Iterable
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict

# Fields that must never be shown. An explicit list, not "everything with the word
# token in it": guessing by name will silently miss one day.
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
        """Don't show secrets even in an error message.

        Not cosmetic. A real case: a test failed on a line with settings, and
        pydantic dumped the whole Gitea token and Google key into the error text —
        in plain view, to the terminal and the log. Any exception, any debug
        line where the settings object shows up leaks keys.

        We fix it here instead of switching the fields to a "secret type": that type is
        always treated as non-empty, and nineteen checks of the form "if the token is set"
        would quietly become always true.
        """
        for name, value in super().__repr_args__():
            if name in _SECRET_FIELDS and value:
                yield name, "***hidden***"
            else:
                yield name, value

    database_url: str = "sqlite:///./vivatlas.db"

    # Empty by default, and that matters. This used to hold the address of a specific
    # server — and any fresh install would go off scanning someone else's Gitea,
    # just because the user never looked in .env. Someone else's address as the default
    # value isn't a trifle — it's someone else's traffic and someone else's load.
    gitea_url: str = ""
    gitea_token: str = ""

    # Needed not for access — the source repositories are public — but so GitHub
    # doesn't cap us at 60 requests per hour. Works without a token too: checking
    # all sources takes 2-3 requests.
    github_token: str = ""

    # Google AI Studio. Verified with live requests on 15.07.2026:
    #   pro (any)            — "quota exceeded", not available for free;
    #   gemini-3.5-flash     — steady 503, not served for free;
    #   gemini-2.5-flash     — 404, removed for new users;
    #   gemini-3.1-flash-lite — works, ~0.8s per request.
    # We don't use *-latest aliases: they silently move to a different model.
    google_api_key: str = ""
    llm_model: str = "gemini-3.1-flash-lite"
    embedding_model: str = "gemini-embedding-2"
    embedding_dim: int = 1536

    http_timeout_seconds: float = 30.0
    llm_timeout_seconds: float = 120.0

    # The free tier is limited by requests per minute. A pause between calls
    # is cheaper than catching a 429 and asking again.
    llm_delay_seconds: float = 1.0

    # The master key to the door. Everything derives from it: signatures for
    # password-reset links and encryption of other people's tokens. Get one: vivatlas secret
    #
    # Changing it means signing everyone out and losing the saved foreign
    # tokens: there'll be nothing left to decrypt them with. Not because it's poorly
    # made, but because the key is the very thing holding them together.
    secret_key: str = ""


settings = Settings()
