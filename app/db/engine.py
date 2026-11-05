"""Engine asincrono in sola lettura e dependency FastAPI della connessione (db-read.md §3).

La URL del DB contiene la password: non va mai nei log né nei messaggi di errore.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.config import Settings

APPLICATION_NAME = "a4u-lms-api"
POOL_RECYCLE_SECONDS = 1800
# Attese massime: apertura di una connessione asyncpg (default 60 s) e connessione libera
# dal pool. Con un DB che non risponde, /health/ready e l'autenticazione falliscono presto.
CONNECT_TIMEOUT_SECONDS = 5
POOL_TIMEOUT_SECONDS = 10


def create_engine(settings: Settings) -> AsyncEngine:
    """Crea l'engine asyncpg: ogni sessione è read-only di default e ha uno statement_timeout.

    `default_transaction_read_only` è una difesa in più: la protezione vera è il ruolo
    `a4u_lms_ro`, che ha solo permessi SELECT.
    """
    return create_async_engine(
        settings.database_url_async,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_recycle=POOL_RECYCLE_SECONDS,
        pool_timeout=POOL_TIMEOUT_SECONDS,
        connect_args={
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": str(settings.db_statement_timeout_ms),
                "application_name": APPLICATION_NAME,
            },
            "timeout": CONNECT_TIMEOUT_SECONDS,
        },
    )


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """Dependency FastAPI: una connessione dal pool di `app.state.engine` per richiesta."""
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        yield conn


ConnDep = Annotated[AsyncConnection, Depends(get_conn)]
