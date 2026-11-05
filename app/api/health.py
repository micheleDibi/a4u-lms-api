"""Endpoint di salute, senza autenticazione e senza rate limit (docs/contracts/api-v1.md §3)."""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db.contract import check_contract
from app.logging import get_logger

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)

# Solo l'esito positivo del controllo del contratto va in cache (per app, su app.state).
CONTRACT_CACHE_SECONDS = 30.0


@router.get("/live")
async def live() -> dict[str, str]:
    """Il processo risponde: nessun accesso al DB."""
    return {"status": "ok"}


@router.get("/ready", responses={503: {"description": "Servizio non pronto"}})
async def ready(request: Request) -> JSONResponse:
    """DB raggiungibile e contratto delle colonne rispettato; il dettaglio solo nel log."""
    state = request.app.state
    try:
        async with state.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            now = time.monotonic()
            if getattr(state, "contract_ok_until", 0.0) <= now:
                await check_contract(conn)
                state.contract_ok_until = now + CONTRACT_CACHE_SECONDS
    except Exception as exc:
        log.warning("health_not_ready", extra={"error_type": type(exc).__name__, "error": str(exc)})
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(content={"status": "ok"})
