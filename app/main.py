"""Factory dell'applicazione FastAPI (docs/contracts/modules.md §2).

Avvio: uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 --workers 1
--no-access-log. Un solo worker: cache dell'autenticazione e rate limit sono in memoria.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.openapi.utils import get_openapi
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

# --- Schema OpenAPI (solo documentazione; esportato in docs/openapi.json) -------------------

API_TITLE = "a4u-lms-api"
API_VERSION = "1"
API_DESCRIPTION = """\
API HTTP di **sola lettura** che espone a un LMS i corsi generati da a4u: organizzazioni, \
corsi, moduli, lezioni, URL pubblici dei file e test di verifica.

- **Autenticazione:** header `Authorization: Bearer <token>` su tutti gli endpoint `/v1`.
- **Errori:** corpo `{"detail": "..."}`; 404 uniforme per risorse inesistenti o non \
accessibili (mai 403).
- **Metodi:** solo GET; gli altri metodi rispondono 405 `Metodo non consentito.` con \
header `Allow`.
- **Limiti:** richieste al minuto per chiave; oltre il limite 429 con `Retry-After`.
- **Date:** ISO 8601 con fuso orario; la parte frazionaria dei secondi è facoltativa \
(assente se zero): usare un parser ISO 8601.
"""
# Solo nello schema esportato: nello schema servito in locale (DOCS_ENABLED=true) «Try it out»
# deve chiamare l'istanza stessa, non la produzione.
SERVERS = [{"url": "https://lms-api.ersaf.it", "description": "Produzione"}]
BEARER_AUTH: dict[str, Any] = {
    "type": "http",
    "scheme": "bearer",
    "description": (
        "Token nel formato `a4u_lms_<prefix8>_<secret>`: `prefix8` sono 8 caratteri "
        "`[a-z0-9]`, il secret (che può contenere `_`) è fornito una sola volta alla creazione "
        "della chiave in a4u."
    ),
}


def _include_routers(app: FastAPI) -> None:
    app.include_router(health_router)
    app.include_router(v1.router)


def _build_openapi(app: FastAPI, *, servers: list[dict[str, str]] | None) -> dict[str, Any]:
    """Schema OpenAPI con `bearerAuth` applicato a tutte (e sole) le operazioni `/v1`."""
    schema = get_openapi(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        routes=app.routes,
        servers=servers,
    )
    schema.setdefault("components", {})["securitySchemes"] = {"bearerAuth": BEARER_AUTH}
    for path, operations in schema.get("paths", {}).items():
        if path.startswith("/v1/"):
            for operation in operations.values():
                operation["security"] = [{"bearerAuth": []}]
    return schema


def openapi_schema() -> dict[str, Any]:
    """Lo schema OpenAPI senza Settings, DB né logging (per `python -m app.cli export-openapi`)."""
    app = FastAPI(title=API_TITLE, version=API_VERSION)
    _include_routers(app)
    return _build_openapi(app, servers=SERVERS)


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
        title=API_TITLE,
        version=API_VERSION,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(AccessLogMiddleware)
    app.add_exception_handler(404, _not_found)
    app.add_exception_handler(405, _method_not_allowed)
    _include_routers(app)

    def _openapi() -> dict[str, Any]:
        # Servito solo con DOCS_ENABLED=true: come docs/openapi.json, ma senza `servers`.
        if app.openapi_schema is None:
            app.openapi_schema = _build_openapi(app, servers=None)
        return app.openapi_schema

    app.openapi = _openapi  # type: ignore[method-assign]
    return app
