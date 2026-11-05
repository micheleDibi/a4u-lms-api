"""Factory dell'applicazione FastAPI (docs/contracts/modules.md §2).

Avvio: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 --workers 1
--no-access-log. Un solo worker: cache dell'autenticazione e rate limit sono in memoria.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api import v1
from app.api.health import router as health_router
from app.auth import Authenticator
from app.config import Settings, get_settings
from app.db.contract import check_contract
from app.db.engine import create_engine
from app.logging import get_logger, setup_logging
from app.middleware import AccessLogMiddleware
from app.ratelimit import RateLimiter

log = get_logger(__name__)

NOT_FOUND_DETAIL = "Risorsa non trovata."
METHOD_NOT_ALLOWED_DETAIL = "Metodo non consentito."


async def _not_found(request: Request, exc: Exception) -> JSONResponse:
    """404 unico: non distingue «non esiste» da «non accessibile», vale anche per i path ignoti."""
    return JSONResponse(status_code=404, content={"detail": NOT_FOUND_DETAIL})


async def _method_not_allowed(request: Request, exc: Exception) -> JSONResponse:
    """405 in italiano; l'header Allow calcolato da Starlette resta invariato."""
    headers = getattr(exc, "headers", None)
    return JSONResponse(
        status_code=405, content={"detail": METHOD_NOT_ALLOWED_DETAIL}, headers=headers
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Crea l'app; il lifespan fallisce se il contratto con il DB di a4u non è rispettato."""
    settings = settings or get_settings()
    setup_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine: AsyncEngine | None = None
        try:
            # Anche un errore nel creare l'engine (es. URL non valida) va nel log di avvio.
            engine = create_engine(settings)
            async with engine.connect() as conn:
                await check_contract(conn)
        except Exception as exc:
            log.error(
                "contract_check_failed",
                extra={"error_type": type(exc).__name__, "error": str(exc)},
            )
            if engine is not None:
                await engine.dispose()
            raise
        app.state.settings = settings
        app.state.engine = engine
        app.state.authenticator = Authenticator(settings.auth_cache_seconds)
        app.state.rate_limiter = RateLimiter(settings.rate_limit_per_min)
        try:
            yield
        finally:
            await engine.dispose()

    docs = settings.docs_enabled
    app = FastAPI(
        title="a4u-lms-api",
        version="1",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_exception_handler(404, _not_found)
    app.add_exception_handler(405, _method_not_allowed)
    app.include_router(health_router)
    app.include_router(v1.router)
    return app
