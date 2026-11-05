# Contratto: moduli, interfacce tra layer, fixture di test

Proprietario: lead. Il dettaglio dei singoli contratti è in `db-read.md`, `api-v1.md`, `files.md` e `config.md`.

## 1. Layout e proprietari

| percorso | contenuto | ruolo |
|---|---|---|
| `pyproject.toml`, `uv.lock`, `docker-compose.test.yml`, `.dockerignore` | progetto, dipendenze, tool, Postgres di test | infra |
| `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`, `.github/**` | deploy, CI, documentazione | infra |
| `app/db/__init__.py`, `app/db/tables.py`, `app/db/engine.py`, `app/db/contract.py`, `app/db/queries.py` | accesso al DB | db |
| `tests/schema.sql`, `tests/conftest.py`, `tests/factories.py`, `tests/db/**` | fixture condivise, test del layer DB | db |
| `app/__init__.py`, `app/main.py`, `app/config.py`, `app/logging.py`, `app/api/__init__.py`, `app/api/health.py` (+ stub di `app/api/v1/__init__.py` in T5) | nucleo dell'app | backend |
| `app/auth.py`, `app/ratelimit.py`, `app/middleware.py`, `tests/core/**` | autenticazione, limiti, log di accesso | backend |
| `app/files.py`, `app/assessment.py`, `app/schemas.py`, `app/api/v1/**`, `app/cli.py`, `tests/unit/**`, `tests/api/**`, `tests/cli/**` | dominio, API v1, CLI | backend-b |

Nessun `__init__.py` nelle cartelle di test: pytest gira con `--import-mode=importlib` e i nomi dei file di test devono essere unici in tutto il progetto (prefisso per cartella: `test_db_*.py`, `test_core_*.py`, `test_unit_*.py`, `test_api_*.py`, `test_cli_*.py`).

## 2. Interfacce tra layer

```python
# app/config.py (backend)
class Settings(BaseSettings): ...          # campi in config.md
def get_settings() -> Settings             # lru_cache; non si usa nei test (i test passano Settings)

# app/db/engine.py (db)
def create_engine(settings: Settings) -> AsyncEngine
async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]   # usa request.app.state.engine
ConnDep = Annotated[AsyncConnection, Depends(get_conn)]

# app/main.py (backend)
def create_app(settings: Settings | None = None) -> FastAPI
```
`create_app` usa `settings or get_settings()` e chiama `setup_logging`. Nel lifespan:
1. crea l'engine;
2. esegue `check_contract` su una connessione; se fallisce → log `event="contract_check_failed"` con il messaggio, poi rilancia, così l'avvio fallisce;
3. imposta `app.state.settings`, `app.state.engine`, `app.state.authenticator` e `app.state.rate_limiter`;
4. alla chiusura esegue `engine.dispose()`.

`create_app` registra inoltre l'`AccessLogMiddleware`, i gestori degli errori 404/500 di `api-v1.md`, `app.api.health.router` e `app.api.v1.router`. Con `DOCS_ENABLED=false` imposta `docs_url`, `redoc_url` e `openapi_url` a `None`.

Avvio: `uvicorn --factory app.main:create_app --host 0.0.0.0 --port 8000 --workers 1 --no-access-log --no-server-header --proxy-headers --forwarded-allow-ips=192.168.40.10` (192.168.40.10 = proxy TLS di prod).

```python
# app/auth.py (backend)
@dataclass(frozen=True, slots=True)
class AuthContext:
    key_id: UUID
    key_prefix: str
    org_ids: frozenset[UUID]

class Authenticator:                       # contiene la cache TTL (api-v1.md §1)
    def __init__(self, cache_seconds: int) -> None: ...
    async def authenticate(self, conn: AsyncConnection, authorization: str | None) -> AuthContext | None

async def require_api_key(request: Request) -> AuthContext
    # NON dipende da ConnDep (P2-1 di R6: un flood non autenticato non deve occupare il pool).
    # 1) formato del token: se non valido -> 401 senza toccare il DB;
    # 2) cache: se c'è una voce valida -> nessuna connessione;
    # 3) solo in caso di mancata cache: `async with request.app.state.engine.connect()` breve,
    #    chiusa prima di proseguire; poi rate limit (429).
AuthDep = Annotated[AuthContext, Depends(require_api_key)]

# app/ratelimit.py (backend)
class RateLimiter:
    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None: ...
    def check(self, key: UUID) -> float | None     # None = consentito; float = secondi di attesa
```
`app/middleware.py` (backend) contiene l'`AccessLogMiddleware`: legge `request.state.key_prefix` se presente (`api-v1.md` §5).

```python
# app/api/v1/__init__.py (backend-b)
router = APIRouter(prefix="/v1")           # include i router di organizations e courses
```
Lo stub con il solo `router` vuoto lo crea backend in T5; dal T9 il file è di backend-b.

Ogni route `/v1`:
- dichiara `ctx: AuthDep` **prima** di `conn: ConnDep` (FastAPI risolve le dipendenze in ordine: un 401/429 non prende connessioni dal pool);
- se `org_id not in ctx.org_ids` → 404 senza query;
- per l'URL base usa `request.app.state.settings.ovh_public_base_url`.

```python
# app/cli.py (backend-b)
python -m app.cli verify-urls --org <uuid> [--course <uuid>] [--concurrency 4] [--timeout 15]
```
- Legge `Settings` dall'ambiente e interroga il DB con le funzioni di `app/db/queries.py`, saltando l'autenticazione HTTP: il comando è per l'operatore sul server.
- Raccoglie tutti gli URL dei file, i loghi compresi, e fa `HEAD` con `httpx.AsyncClient`, seguendo i redirect.
- Stampa un report degli URL con risposta diversa da 200 (status oppure errore) e un riepilogo.
- Codice di uscita: 0 se tutti rispondono 200, 1 se almeno uno no, 2 per un errore di configurazione o un'org/corso inesistente.
- La funzione che fa le HEAD accetta un `httpx.AsyncBaseTransport` opzionale, per i test.

## 3. Dipendenze (pyproject, task 1)
- **Runtime:** `fastapi`, `uvicorn[standard]` (o `uvicorn` semplice, a scelta di infra), `sqlalchemy[asyncio]>=2`, `asyncpg`, `pydantic>=2`, `pydantic-settings`, `httpx`.
- **Sviluppo:** `pytest`, `pytest-asyncio`, `ruff`, `mypy`.
- Nessun'altra dipendenza senza una richiesta `CONTRATTO:`.

Configurazione dei tool:
- **ruff:** `line-length = 100`, `target-version = "py312"`, regole `E, F, W, I, B, UP, SIM, ASYNC, S` (con `S101`, `S105`, `S106`, `S608` ignorate in `tests/**`).
- **mypy:** `strict = true` su `app/`, plugin `pydantic.mypy`.
- **pytest:** `asyncio_mode = "auto"`, `asyncio_default_fixture_loop_scope = "session"`, `asyncio_default_test_loop_scope = "session"`, `addopts = "--import-mode=importlib -q"`, `testpaths = ["tests"]`.

## 4. Fixture di test condivise (`tests/conftest.py`, ruolo db)
Nessuna fixture DB è `autouse`: i test in `tests/unit/**` girano senza Postgres.

| fixture | scope | cosa fornisce |
|---|---|---|
| `pg_admin_url` | session | da env `TEST_PG_ADMIN_URL`, default `postgresql://postgres:postgres@127.0.0.1:55432/postgres` |
| `test_db` | session | ricrea il DB di nome `TEST_DB_NAME` (env, default `a4u_lms_test`; il gate usa `a4u_lms_test_<ruolo>`), applica `tests/schema.sql`, crea o aggiorna il ruolo `a4u_lms_ro` (password `test_ro_password`) con **gli stessi** comandi del runbook §3.2 di a4u (read-only di default, CONNECT, USAGE, SELECT sulle 6 tabelle, SELECT su `users(id, full_name, email)`); restituisce la URL admin del DB di test |
| `ro_database_url` | session | `postgresql://a4u_lms_ro:test_ro_password@<host>:<porta>/<TEST_DB_NAME>` (host e porta presi da `pg_admin_url`) |
| `admin_conn` | function | `AsyncConnection` SQLAlchemy come `postgres` sul DB di test, in `AUTOCOMMIT`; a fine test esegue `TRUNCATE` di tutte le tabelle con `CASCADE` |
| `make_settings` | function | `(**overrides) -> Settings`: `DATABASE_URL = ro_database_url`, `OVH_PUBLIC_BASE_URL = "https://media.test/media"`, `RATE_LIMIT_PER_MIN = 10000`, gli altri default di `config.md` |
| `ro_conn` | function | `AsyncConnection` da `create_engine(make_settings())`, cioè come `a4u_lms_ro` |
| `client_factory` | function | `(settings: Settings \| None = None) -> AsyncContextManager[httpx.AsyncClient]`: esegue il lifespan di `create_app(settings)` e restituisce un client `ASGITransport` con `base_url="http://test"` |
| `client` | function | `client_factory()` con le impostazioni di default |

Import nei test: `from app.… import …` e `from tests.factories import …` (pytest `pythonpath = ["."]`).

`tests/factories.py`: funzioni `async` con primo argomento `admin_conn`, che restituiscono l'`UUID` creato.
- `create_user(conn, *, full_name="Docente Test", email=None)`
- `create_org(conn, *, name="Org", website=None, logo_path=None, deleted_at=None)`
- `create_course(conn, *, org_id, assignee_id, **cols)`: default `status="draft"`, `cfu=6`, `language_code="it"`; `cols` sovrascrive qualunque colonna, anche `updated_at`
- `create_module(conn, *, course_id, position, **cols)`
- `create_lesson(conn, *, module_id, course_id, position, **cols)`: default di tutti gli status `"empty"`, `is_assessment=False`
- `create_api_key(conn, *, org_ids, revoked_at=None, expires_at=None, key_prefix=None) -> str`: restituisce il **token in chiaro** generato con la formula di a4u (`secrets.token_urlsafe(32)`; un `key_prefix` casuale `[a-z0-9]{8}` se non è indicato)
- `make_token(key_prefix: str, secret: str) -> tuple[str, str]`: (token, key_hash), per i test di formato
