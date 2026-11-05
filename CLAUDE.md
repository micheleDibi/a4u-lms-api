# Progetto: a4u-lms-api

Servizio HTTP di **sola lettura** che espone a un LMS esterno i corsi generati da a4u
(organizzazioni, corsi, moduli, lezioni, URL dei file su OVH, test di verifica).
Gira in produzione sulla VM web2 (192.168.40.13, la stessa di a4u) in un docker compose separato,
collegato alla rete `a4u_default`, e legge il Postgres di a4u con il ruolo `a4u_lms_ro`.

## Stack
- Python 3.12, FastAPI, SQLAlchemy 2 async in stile **Core** (niente ORM) + asyncpg, pydantic-settings
- Gestione del progetto: uv · lint e format: ruff (100 colonne, py312) · tipi: mypy strict su `app/` · test: pytest + pytest-asyncio
- Database: il Postgres 16 di a4u (non è nostro: **nessuna migrazione, nessun Alembic, nessuna scrittura**)

## Mappa del codice
- `app/main.py`: `create_app()` (factory per uvicorn `--factory`), lifespan con il controllo del contratto
- `app/config.py`: `Settings` (variabili in `docs/contracts/config.md`)
- `app/db/`: `tables.py` (Table e Column minimali), `engine.py`, `contract.py`, `queries.py` (tutto l'SQL)
- `app/auth.py`, `app/ratelimit.py`, `app/middleware.py`: autenticazione Bearer, rate limit, log di accesso
- `app/files.py`, `app/assessment.py`: URL dei file su OVH, parsing tollerante dei test
- `app/schemas.py`, `app/api/v1/`: modelli di risposta e router `/v1`; `app/api/health.py`
- `app/cli.py`: `python -m app.cli verify-urls --org <uuid> [--course <uuid>]`
- `tests/`: `schema.sql` (DDL del sottoinsieme del contratto, rif. a4u 0045), `conftest.py`, `factories.py`, `db/`, `core/`, `unit/`, `api/`, `cli/`
- Contratti: `docs/contracts/` (sono la specifica: leggerli prima di scrivere codice)

## Comandi
- Dipendenze: `uv sync`
- Postgres di test: `docker compose -f docker-compose.test.yml up -d` (porta `127.0.0.1:55432`)
- Test di un modulo: `uv run pytest tests/api/test_api_courses.py`
- Test completi: `uv run pytest` (meno di 1 minuto, con il Postgres di test attivo)
- Lint e format: `uv run ruff check . && uv run ruff format --check .`
- Tipi: `uv run mypy app`
- Avvio locale (solo se serve, non lasciarlo in esecuzione): `uv run uvicorn --factory app.main:create_app --port 8000`

## Convenzioni
- Codice e identificatori in inglese; commenti, docstring, documentazione e messaggi di log descrittivi in italiano.
- Commit: Conventional Commits con la descrizione in italiano (`feat(api): …`).
- Tutto l'SQL sta in `app/db/queries.py`: mai `SELECT *`, colonne esplicite, nessun N+1.
- Errori HTTP: 401 con un unico messaggio generico, 404 (mai 403) per le risorse non accessibili.
- Log in JSON (stdlib). Mai nei log: il token, l'header Authorization, `DATABASE_URL`, il testo delle domande.

## Contratto con a4u
- La fonte è `a4u/docs/lms-api.md` (repo `/Users/micheledibisceglia/Developer/a4u`, **sola lettura**, non si importa codice di a4u).
- Colonne lette: `docs/contracts/db-read.md`. Se a4u rinomina o toglie una colonna, il servizio non parte e `/health/ready` va in 503.
- Gli URL dei file replicano `uploads_key`, `pdf_key` e `media_url` di `a4u/backend/app/services/remote_storage.py`.

## Da non toccare
- Il repository a4u e il server di produzione (`ssh a4u-prod` solo in lettura).
- `.env` (non versionato).

## Trappole note
- **Un solo worker uvicorn:** cache dell'autenticazione e rate limit sono in memoria per processo.
- **Cache dell'autenticazione (30 s di default):** una revoca ha effetto entro `AUTH_CACHE_SECONDS`.
- Prod ha `STORAGE_LOCAL_FALLBACK=true`: alcuni file vecchi potrebbero non esistere su OVH (va controllato con `verify-urls`).
- Il secret del token può contenere `_`: il token si legge per posizione, mai con `split("_")`.
- `information_schema.columns` mostra solo le colonne su cui il ruolo ha un permesso: è voluto, il controllo del contratto verifica anche i GRANT.
- Al 05/11/2026 il ruolo `a4u_lms_ro` **non esiste** in prod: prima del deploy va eseguito il runbook §3 di a4u.
- Il proxy TLS di prod è su 192.168.40.10 (openresty), non sulla VM: per questo la porta è pubblicata su `192.168.40.13:9002`.

## cteam
- Ruoli e cartelle: `infra` → file di progetto, Docker, CI, README · `db` → `app/db/`, `tests/schema.sql`, `tests/conftest.py`, `tests/factories.py`, `tests/db/` · `backend` → nucleo dell'app, auth, `tests/core/` · `backend-b` → dominio, API v1, CLI, `tests/unit|api|cli/` · `reviewer` → `docs/reviews/`
- Porte: servizio 8000 (container), Postgres di test 55432
- Contratti: `docs/contracts/`
- Gate: `.cteam/gate.sh` (ruff, format, mypy, pytest sulle cartelle del ruolo)
- Isolamento: `shared`
