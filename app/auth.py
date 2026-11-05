"""Autenticazione Bearer con le chiavi API di a4u (docs/contracts/api-v1.md §1).

Formato: token = "a4u_lms_" + key_prefix + "_" + secret. Il secret può contenere "_",
quindi il token si legge per posizione e mai con split("_").
Il token non compare mai nei log né come chiave della cache (si usa il suo sha256).
"""

import hashlib
import hmac
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.queries import fetch_key_org_ids, fetch_valid_api_key
from app.ratelimit import RateLimiter

TOKEN_PREFIX = "a4u_lms_"  # noqa: S105 (prefisso fisso del formato, non un segreto)
MAX_TOKEN_LENGTH = 256
_KEY_PREFIX_RE = re.compile(r"[a-z0-9]{8}")

UNAUTHORIZED_DETAIL = "Credenziali non valide o mancanti."
TOO_MANY_REQUESTS_DETAIL = "Troppe richieste."


@dataclass(frozen=True, slots=True)
class AuthContext:
    key_id: UUID
    key_prefix: str
    org_ids: frozenset[UUID]


def parse_authorization(authorization: str | None) -> tuple[str, str] | None:
    """Da `Authorization: Bearer <token>` a (token, key_prefix); None se il formato è errato."""
    if not authorization:
        return None
    scheme, sep, credentials = authorization.partition(" ")
    if not sep or scheme.lower() != "bearer":
        return None
    token = credentials.strip()
    if len(token) > MAX_TOKEN_LENGTH or not token.startswith(TOKEN_PREFIX):
        return None
    key_prefix = token[8:16]
    if not _KEY_PREFIX_RE.fullmatch(key_prefix):
        return None
    if token[16:17] != "_" or not token[17:]:
        return None
    return token, key_prefix


class Authenticator:
    """Valida i token sul DB, con una cache TTL dei soli esiti positivi.

    Chiave della cache: sha256 del token. Durata: min(now + cache_seconds, expires_at).
    Con cache_seconds = 0 la cache è disattivata. `clock` (secondi epoch) serve ai test.
    Il pool si tocca solo in caso di mancata cache: formato errato e voci in cache non
    prendono connessioni (P2-1 di R6).
    """

    def __init__(self, cache_seconds: int, *, clock: Callable[[], float] = time.time) -> None:
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, AuthContext]] = {}

    async def authenticate(
        self, engine: AsyncEngine, authorization: str | None
    ) -> AuthContext | None:
        parsed = parse_authorization(authorization)
        if parsed is None:
            return None
        token, key_prefix = parsed
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()

        now = self._clock()
        cached = self._cache.get(digest)
        if cached is not None:
            expires, ctx = cached
            if now < expires:
                return ctx
            del self._cache[digest]

        # Connessione breve, restituita al pool prima di proseguire con la richiesta.
        async with engine.connect() as conn:
            row = await fetch_valid_api_key(conn, key_prefix)
            if row is None or not hmac.compare_digest(digest, row.key_hash):
                return None
            org_ids = await fetch_key_org_ids(conn, row.id)
        ctx = AuthContext(key_id=row.id, key_prefix=row.key_prefix, org_ids=org_ids)

        if self._cache_seconds > 0:
            expires = now + self._cache_seconds
            if row.expires_at is not None:
                expires = min(expires, row.expires_at.timestamp())
            if expires > now:
                self._purge_expired(now)
                self._cache[digest] = (expires, ctx)
        return ctx

    def _purge_expired(self, now: float) -> None:
        for digest in [d for d, (expires, _) in self._cache.items() if expires <= now]:
            del self._cache[digest]


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail=UNAUTHORIZED_DETAIL, headers={"WWW-Authenticate": "Bearer"}
    )


async def require_api_key(request: Request) -> AuthContext:
    """Autentica la richiesta (401), registra key_prefix per il log, applica il rate limit (429).

    Non dipende da ConnDep: un 401 per formato errato o un hit di cache non usano il pool.
    """
    authorization = request.headers.get("authorization")
    parsed = parse_authorization(authorization)
    if parsed is None:
        raise _unauthorized()
    # Prima del 401: anche il log di accesso dei tentativi falliti ha il prefisso.
    request.state.key_prefix = parsed[1]

    authenticator: Authenticator = request.app.state.authenticator
    ctx = await authenticator.authenticate(request.app.state.engine, authorization)
    if ctx is None:
        raise _unauthorized()

    rate_limiter: RateLimiter = request.app.state.rate_limiter
    wait = rate_limiter.check(ctx.key_id)
    if wait is not None:
        raise HTTPException(
            status_code=429,
            detail=TOO_MANY_REQUESTS_DETAIL,
            headers={"Retry-After": str(max(1, math.ceil(wait)))},
        )
    return ctx


AuthDep = Annotated[AuthContext, Depends(require_api_key)]
