"""Opzioni dell'engine (db-read.md §3), senza aprire connessioni."""

from typing import Any
from unittest.mock import patch

from app.config import Settings
from app.db import engine as engine_module


def test_engine_options() -> None:
    settings = Settings(
        database_url="postgresql://a4u_lms_ro:secret@db.test:5432/a4u",
        ovh_public_base_url="https://media.test/media",
        db_pool_size=2,
        db_max_overflow=1,
        db_statement_timeout_ms=2500,
    )
    captured: dict[str, Any] = {}

    def _fake_create_async_engine(url: str, **kwargs: Any) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return object()

    with patch.object(engine_module, "create_async_engine", _fake_create_async_engine):
        engine_module.create_engine(settings)

    assert captured["url"] == "postgresql+asyncpg://a4u_lms_ro:secret@db.test:5432/a4u"
    assert (captured["pool_size"], captured["max_overflow"]) == (2, 1)
    assert captured["pool_pre_ping"] is True
    assert (captured["pool_recycle"], captured["pool_timeout"]) == (1800, 10)
    assert captured["connect_args"] == {
        "server_settings": {
            "default_transaction_read_only": "on",
            "statement_timeout": "2500",
            "application_name": "a4u-lms-api",
        },
        "timeout": 5,
    }
