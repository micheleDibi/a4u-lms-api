"""Log di accesso e gestione degli errori inattesi (docs/contracts/api-v1.md §2 e §5).

Middleware ASGI puro: scrive una riga JSON `event="access"` per ogni richiesta HTTP con
key_prefix (o null), method, path (senza query string), status e duration_ms.
Mai nei log: header (Authorization compreso), token, query string.
"""

import time
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.logging import get_logger

access_log = get_logger("app.access")
error_log = get_logger("app.error")

INTERNAL_ERROR_DETAIL = "Errore interno."


class AccessLogMiddleware:
    """Registra ogni richiesta e trasforma le eccezioni non gestite in un 500 generico.

    Il 500 è gestito qui e non con un exception handler di Starlette: quello gira nel
    ServerErrorMiddleware, esterno a questo middleware, e rilancia sempre l'eccezione.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # request.state legge e scrive in scope["state"]: la dependency di autenticazione
        # ci mette key_prefix, che qui si rilegge a fine richiesta.
        state: dict[str, Any] = scope.setdefault("state", {})
        method: str = scope["method"]
        path: str = scope["path"]
        # None finché la risposta non parte: una cancellazione (client disconnesso,
        # CancelledError) si logga con status null e si rilancia, senza fingere un 500.
        status: int | None = None
        response_started = False
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            error_log.exception("unhandled_exception", extra={"method": method, "path": path})
            # Se la risposta è già partita, il log tiene lo status effettivamente inviato.
            if not response_started:
                status = 500
                response = JSONResponse(status_code=500, content={"detail": INTERNAL_ERROR_DETAIL})
                await response(scope, receive, send)
        finally:
            access_log.info(
                "access",
                extra={
                    "key_prefix": state.get("key_prefix"),
                    "method": method,
                    "path": path,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
