# Contratto: configurazione, porte, log

Proprietario: lead.

## 1. Variabili d'ambiente (`app/config.py`, `Settings(BaseSettings)`)

`model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)`.
Il file `.env` lo legge docker compose, non l'app.

| variabile | campo | tipo | default | note |
|---|---|---|---|---|
| `DATABASE_URL` | `database_url` | `SecretStr` | **obbligatoria** | utente `a4u_lms_ro`; accetta `postgresql://`, `postgres://`, `postgresql+asyncpg://` |
| `OVH_PUBLIC_BASE_URL` | `ovh_public_base_url` | `str` | **obbligatoria** | es. `https://progettiersaf.com/media`; deve iniziare con `http://` o `https://`; lo `/` finale si toglie |
| `RATE_LIMIT_PER_MIN` | `rate_limit_per_min` | `int` | `120` | ≥ 1 |
| `AUTH_CACHE_SECONDS` | `auth_cache_seconds` | `int` | `30` | ≥ 0; 0 = cache disattivata |
| `DB_STATEMENT_TIMEOUT_MS` | `db_statement_timeout_ms` | `int` | `10000` | ≥ 100 |
| `DB_POOL_SIZE` | `db_pool_size` | `int` | `3` | ≥ 1 |
| `DB_MAX_OVERFLOW` | `db_max_overflow` | `int` | `4` | ≥ 0 |
| `DOCS_ENABLED` | `docs_enabled` | `bool` | `false` | abilita `/docs`, `/redoc` e `/openapi.json` |
| `LOG_LEVEL` | `log_level` | `str` | `INFO` | `DEBUG`, `INFO`, `WARNING` o `ERROR` |

Proprietà `database_url_async: str`: la URL con il driver `postgresql+asyncpg://`.
`repr(settings)` non deve mai mostrare la password (per questo `SecretStr`).

## 2. Porte
- Nel container uvicorn ascolta su `0.0.0.0:8000`, con **un solo worker**.
- In prod la porta è pubblicata su `192.168.40.13:9002`, l'IP privato della VM web2 (scelta dell'utente: il proxy TLS su 192.168.40.10 non raggiungerebbe `127.0.0.1`).
- Hostname pubblico: `lms-api.ersaf.it`, con proxy host su 192.168.40.10 che punta a `http://192.168.40.13:9002`.
- Postgres di test in locale: `127.0.0.1:55432` (`docker-compose.test.yml`, `postgres:16-alpine`, utente e password `postgres`/`postgres`).
- In prod: rete Docker esterna `a4u_default`, host del DB `postgres:5432`.

## 3. Log (`app/logging.py`)
```python
def setup_logging(level: str) -> None
def get_logger(name: str) -> logging.Logger
```
- Solo libreria standard. Un `logging.Formatter` scrive una riga JSON per record, con i campi `ts` (ISO 8601 UTC), `level`, `logger`, `event` (il messaggio) e gli extra passati con `extra={...}`.
- Le eccezioni finiscono nel campo `exc` (traceback come stringa).
- `setup_logging` configura il root logger e i logger `uvicorn` e `uvicorn.error` sullo stdout in JSON. Il logger `uvicorn.access` è disattivato.
- Uso: `log.warning("unsafe_file_path", extra={"lesson_id": str(id), "kind": "dispensa"})`.
- Il formatter ignora gli attributi standard di `LogRecord` e serializza gli extra con `default=str`.
- **Mai nei log:** il token, l'header `Authorization`, `DATABASE_URL`, il contenuto delle domande.
