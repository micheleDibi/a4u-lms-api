"""Test senza DB: Settings, formatter JSON, RateLimiter."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.logging import JsonFormatter, setup_logging
from app.ratelimit import RateLimiter

DB_PASSWORD = "pw-segreta-123"  # noqa: S105 - password finta dei test
DB_URL = f"postgresql://a4u_lms_ro:{DB_PASSWORD}@db.test:5432/a4u"
OVH = "https://media.test/media"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le variabili dell'ambiente reale non devono influenzare i test di Settings."""
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


# --- Settings ---------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["DATABASE_URL", "OVH_PUBLIC_BASE_URL"])
def test_settings_missing_required_is_error(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    env = {"DATABASE_URL": DB_URL, "OVH_PUBLIC_BASE_URL": OVH}
    del env[missing]
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    assert missing.lower() in str(exc_info.value)


def test_settings_from_env_with_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("OVH_PUBLIC_BASE_URL", OVH)
    settings = Settings()
    assert settings.rate_limit_per_min == 120
    assert settings.auth_cache_seconds == 30
    assert settings.db_statement_timeout_ms == 10000
    assert settings.db_pool_size == 3
    assert settings.db_max_overflow == 4
    assert settings.docs_enabled is False
    assert settings.log_level == "INFO"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
        ("postgresql+asyncpg://u:p@h:5432/d", "postgresql+asyncpg://u:p@h:5432/d"),
    ],
)
def test_settings_database_url_async(url: str, expected: str) -> None:
    settings = Settings(database_url=url, ovh_public_base_url=OVH)
    assert settings.database_url_async == expected


def test_settings_repr_hides_password() -> None:
    settings = Settings(database_url=DB_URL, ovh_public_base_url=OVH)
    assert DB_PASSWORD not in repr(settings)
    assert DB_PASSWORD not in str(settings)
    assert DB_PASSWORD not in str(settings.model_dump())


@pytest.mark.parametrize(
    "bad_url",
    [
        f"mysql://u:{DB_PASSWORD}@h/d",
        f"postgresql://u:{DB_PASSWORD}@db.test:noport/a4u",  # porta non numerica
        f"postgresql://u:{DB_PASSWORD}@db.test:99999/a4u",  # porta fuori range
        f"postgres://u:{DB_PASSWORD}@db.test:0/a4u",
    ],
)
def test_settings_invalid_database_url_hides_password(bad_url: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(database_url=bad_url, ovh_public_base_url=OVH)
    error = exc_info.value
    assert error.errors()[0]["loc"] == ("database_url",)
    # str() è ciò che finisce nel traceback di avvio (hide_input_in_errors); errors() espone
    # sempre l'input, ma nessun codice dell'app lo usa.
    assert DB_PASSWORD not in str(error)
    assert DB_PASSWORD not in repr(error.errors(include_input=False))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://media.test/media/", "https://media.test/media"),
        ("https://media.test/media", "https://media.test/media"),
        ("http://media.test//", "http://media.test"),
    ],
)
def test_settings_ovh_base_url_strips_trailing_slash(value: str, expected: str) -> None:
    assert Settings(database_url=DB_URL, ovh_public_base_url=value).ovh_public_base_url == expected


@pytest.mark.parametrize(
    "value",
    [
        "ftp://media.test",
        "media.test/media",
        "https://",
        "",
        "https://media.test/media?sig=abc",
        "https://media.test/media#frag",
        "https://media.test/media?",
    ],
)
def test_settings_ovh_base_url_requires_http_scheme(value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL, ovh_public_base_url=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("rate_limit_per_min", 0),
        ("auth_cache_seconds", -1),
        ("db_statement_timeout_ms", 99),
        ("db_pool_size", 0),
        ("db_max_overflow", -1),
        ("log_level", "TRACE"),
    ],
)
def test_settings_out_of_range_is_error(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DB_URL, ovh_public_base_url=OVH, **{field: value})


def test_settings_log_level_case_insensitive() -> None:
    assert Settings(database_url=DB_URL, ovh_public_base_url=OVH, log_level="debug").log_level == (
        "DEBUG"
    )


# --- Formatter JSON ---------------------------------------------------------------------


def _record(**kwargs: object) -> logging.LogRecord:
    logger = logging.getLogger("test.core.formatter")
    return logger.makeRecord(
        logger.name,
        logging.WARNING,
        __file__,
        1,
        "unsafe_file_path",
        (),
        kwargs.pop("exc_info", None),  # type: ignore[arg-type]
        extra=kwargs or None,
    )


def test_formatter_writes_base_fields_and_extra() -> None:
    lesson_id = uuid4()
    line = JsonFormatter().format(_record(lesson_id=lesson_id, kind="dispensa"))
    payload = json.loads(line)
    assert "\n" not in line
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "test.core.formatter"
    assert payload["event"] == "unsafe_file_path"
    assert payload["lesson_id"] == str(lesson_id)  # default=str
    assert payload["kind"] == "dispensa"
    assert "exc" not in payload
    ts = datetime.fromisoformat(payload["ts"])
    assert ts.utcoffset() == timedelta(0)
    # Gli attributi standard di LogRecord non finiscono nel JSON.
    for standard in ("msg", "args", "pathname", "lineno", "levelno", "created", "thread"):
        assert standard not in payload


def test_formatter_writes_exception_traceback() -> None:
    try:
        raise ValueError("errore di prova")
    except ValueError:
        record = _record(exc_info=sys.exc_info())
    payload = json.loads(JsonFormatter().format(record))
    assert "Traceback" in payload["exc"]
    assert "ValueError: errore di prova" in payload["exc"]


def test_formatter_keeps_extra_with_reserved_name() -> None:
    payload = json.loads(JsonFormatter().format(_record(event="altro", level="x")))
    assert payload["event"] == "unsafe_file_path"
    assert payload["level"] == "WARNING"
    assert payload["extra_event"] == "altro"
    assert payload["extra_level"] == "x"


def test_setup_logging_is_idempotent_and_keeps_foreign_handlers() -> None:
    root = logging.getLogger()
    foreign = logging.NullHandler()
    root.addHandler(foreign)
    try:
        setup_logging("INFO")
        setup_logging("DEBUG")
        json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
        assert len(json_handlers) == 1
        assert foreign in root.handlers
        assert root.level == logging.DEBUG
        assert logging.getLogger("uvicorn.access").disabled is True
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("httpcore").level == logging.WARNING
    finally:
        root.removeHandler(foreign)
        setup_logging("INFO")


# --- RateLimiter ------------------------------------------------------------------------


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_rate_limiter_bucket_capacity_and_refill() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=6, clock=clock)  # 1 gettone ogni 10 s
    key = uuid4()
    assert [limiter.check(key) for _ in range(6)] == [None] * 6
    wait = limiter.check(key)
    assert wait == pytest.approx(10.0)
    clock.now += 4.0
    assert limiter.check(key) == pytest.approx(6.0)
    clock.now += 6.0
    assert limiter.check(key) is None
    assert limiter.check(key) == pytest.approx(10.0)


def test_rate_limiter_refill_is_capped_and_per_key() -> None:
    clock = FakeClock()
    limiter = RateLimiter(per_minute=2, clock=clock)
    first, second = uuid4(), uuid4()
    assert limiter.check(first) is None
    assert limiter.check(first) is None
    assert limiter.check(first) is not None
    assert limiter.check(second) is None  # bucket indipendente
    clock.now += 3600.0  # molto tempo: si torna alla capacità, non oltre
    assert [limiter.check(first) for _ in range(3)][:2] == [None, None]
    assert limiter.check(first) is not None


def test_rate_limiter_rejects_invalid_capacity() -> None:
    with pytest.raises(ValueError, match="per_minute"):
        RateLimiter(per_minute=0)
