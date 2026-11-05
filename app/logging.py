"""Log in JSON su stdout con la sola libreria standard (docs/contracts/config.md §3).

Mai nei log: il token, l'header Authorization, DATABASE_URL, il testo delle domande.
"""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

# Attributi standard di LogRecord: tutto il resto arriva da `extra={...}`.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None)).keys()
    | {"message", "asctime", "taskName"}
)

# Marca l'handler installato da setup_logging, per sostituire solo quello.
_HANDLER_MARK = "_a4u_lms_json"


class JsonFormatter(logging.Formatter):
    """Una riga JSON per record: ts, level, logger, event, gli extra ed eventualmente exc."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS:
                # Un extra con il nome di un campo base non lo sovrascrive e non va perso.
                payload[f"extra_{key}" if key in payload else key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["exc"] = record.exc_text
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def _json_handler() -> logging.Handler:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    return handler


def setup_logging(level: str) -> None:
    """Configura root, `uvicorn` e `uvicorn.error` in JSON su stdout; spegne `uvicorn.access`.

    Idempotente: sostituisce solo l'handler installato in precedenza da questa funzione,
    lasciando gli altri (per esempio quelli di cattura dei test).
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if getattr(existing, _HANDLER_MARK, False):
            root.removeHandler(existing)
    root.addHandler(_json_handler())
    root.setLevel(level.upper())

    for name in ("uvicorn", "uvicorn.error"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
        uv_logger.setLevel(level.upper())

    # Il client httpx (CLI, test) a INFO scriverebbe gli URL con la query string.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True


def get_logger(name: str) -> logging.Logger:
    """Logger con nome `name`, che scrive tramite il root logger configurato."""
    return logging.getLogger(name)
