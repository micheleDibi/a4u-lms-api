"""Configurazione del servizio letta dalle variabili d'ambiente (docs/contracts/config.md §1)."""

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

_ASYNC_SCHEME = "postgresql+asyncpg://"
_SYNC_SCHEMES = ("postgresql://", "postgres://")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class Settings(BaseSettings):
    """Impostazioni del servizio. `repr` non mostra mai la password del DB."""

    # hide_input_in_errors: un errore di validazione non deve stampare DATABASE_URL.
    model_config = SettingsConfigDict(
        env_file=None, extra="ignore", case_sensitive=False, hide_input_in_errors=True
    )

    database_url: SecretStr
    ovh_public_base_url: str
    rate_limit_per_min: int = Field(default=120, ge=1)
    auth_cache_seconds: int = Field(default=30, ge=0)
    db_statement_timeout_ms: int = Field(default=10000, ge=100)
    db_pool_size: int = Field(default=3, ge=1)
    db_max_overflow: int = Field(default=4, ge=0)
    docs_enabled: bool = False
    log_level: LogLevel = "INFO"

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, value: SecretStr) -> SecretStr:
        url = value.get_secret_value()
        if not url.startswith((_ASYNC_SCHEME, *_SYNC_SCHEMES)):
            raise ValueError(
                "DATABASE_URL deve iniziare con postgresql://, postgres:// o postgresql+asyncpg://"
            )
        # Il messaggio di make_url può contenere la URL con la password: messaggio generico e
        # `from None`, così l'eccezione originale non compare nemmeno nel traceback.
        try:
            port = make_url(url).port
        except Exception:
            raise ValueError("DATABASE_URL non valida") from None
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("DATABASE_URL non valida")
        return value

    @field_validator("ovh_public_base_url")
    @classmethod
    def _check_ovh_public_base_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            raise ValueError("OVH_PUBLIC_BASE_URL deve iniziare con http:// o https://")
        # Gli URL dei file si costruiscono accodando il percorso: query e fragment li romperebbero.
        if "?" in url or "#" in url:
            raise ValueError("OVH_PUBLIC_BASE_URL non può contenere query string o fragment")
        return url

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @property
    def database_url_async(self) -> str:
        """La URL del DB con il driver asyncpg (contiene la password: mai nei log)."""
        url = self.database_url.get_secret_value()
        for scheme in _SYNC_SCHEMES:
            if url.startswith(scheme):
                return _ASYNC_SCHEME + url[len(scheme) :]
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Impostazioni lette dall'ambiente, una volta per processo (i test passano Settings)."""
    return Settings()
