# Contratto: accesso al database di a4u (sola lettura)

Proprietario: lead. Ogni modifica passa da una richiesta `CONTRATTO:` al lead.
Fonte a monte: `a4u/docs/lms-api.md` §1 (blocco `lms-read-contract`) e modelli SQLAlchemy di a4u
alla migrazione **0045**. Il servizio non possiede schema: nessuna migrazione, nessun DDL.

## 1. Tabelle e colonne (le sole dichiarate in `app/db/tables.py`)

Tipi Postgres reali di a4u (servono per `tests/schema.sql`).

| tabella | colonne |
|---|---|
| `organizations` | `id uuid PK`, `name varchar(255) NOT NULL`, `website varchar(255) NULL`, `logo_path varchar(500) NULL`, `deleted_at timestamptz NULL`, `created_at timestamptz NOT NULL`, `updated_at timestamptz NOT NULL` |
| `course` | `id uuid PK`, `organization_id uuid NOT NULL FK organizations ON DELETE CASCADE`, `title varchar(200) NOT NULL`, `status varchar(40) NOT NULL`, `language_code varchar(10) NOT NULL`, `cfu smallint NOT NULL`, `corso_di_laurea varchar(200) NULL`, `objectives text NOT NULL DEFAULT ''`, `argomenti_chiave jsonb NOT NULL DEFAULT '[]'`, `course_overview text NULL`, `assignee_user_id uuid NOT NULL FK users ON DELETE RESTRICT`, `created_at timestamptz NOT NULL`, `updated_at timestamptz NOT NULL` |
| `course_module` | `id uuid PK`, `course_id uuid NOT NULL FK course ON DELETE CASCADE`, `position smallint NOT NULL`, `module_code varchar(20) NOT NULL`, `title varchar(300) NOT NULL`, `description text NOT NULL DEFAULT ''` |
| `course_lesson` | `id uuid PK`, `module_id uuid NOT NULL FK course_module`, `course_id uuid NOT NULL FK course`, `position smallint NOT NULL`, `lesson_code varchar(30) NOT NULL`, `title varchar(300) NOT NULL`, `summary text NOT NULL DEFAULT ''`, `is_introductory bool NOT NULL`, `is_assessment bool NOT NULL`, `learning_objectives jsonb NOT NULL`, `mandatory_topics jsonb NOT NULL`, `content_status varchar(40) NOT NULL`, `content_approved_at timestamptz NULL`, `content_raw jsonb NULL`, `pdf_status varchar(40) NOT NULL`, `pdf_path varchar(500) NULL`, `pdf_generated_at timestamptz NULL`, `slides_pdf_status`, `slides_pdf_path`, `slides_pdf_generated_at`, `speech_pdf_status`, `speech_pdf_path`, `speech_pdf_generated_at`, `video_status`, `video_path`, `video_generated_at`, `video_tokens jsonb NULL`, `avatar_video_status`, `avatar_video_path`, `avatar_video_generated_at` (stessi tipi delle colonne `pdf_*` corrispondenti) |
| `users` | `id uuid PK`, `full_name varchar(255) NOT NULL`, `email citext NOT NULL` — il ruolo ha `SELECT` **solo** su queste tre colonne |
| `lms_api_keys` | `id uuid PK`, `name varchar(120) NOT NULL`, `key_prefix varchar(16) NOT NULL UNIQUE`, `key_hash char(64) NOT NULL UNIQUE`, `expires_at timestamptz NULL`, `revoked_at timestamptz NULL` |
| `lms_api_key_organizations` | `api_key_id uuid FK lms_api_keys ON DELETE CASCADE`, `organization_id uuid FK organizations ON DELETE CASCADE`, PK composta |

`tests/schema.sql` può aggiungere **solo** `users.password_hash varchar(255) NOT NULL` (serve al test
del permesso per colonna) e i vincoli CHECK/UNIQUE/FK di a4u sulle colonne elencate.

## 2. Regole di accesso (obbligatorie)

- Mai `SELECT *`, mai `select(table)` senza colonne esplicite: si selezionano le colonne servite.
- `course_lesson.content_raw` si legge **solo** con
  `CASE WHEN is_assessment AND content_status IN ('ready','approved') THEN content_raw END`.
- `video_tokens` non si legge intero: solo `video_tokens -> 'video_duration_s'` (jsonb, parsing in Python).
- Organizzazioni: sempre `organizations.deleted_at IS NULL`, anche passando da `lms_api_key_organizations`.
- Chiave valida: `revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now())` (orologio del DB).
- Dettaglio corso: **3 query fisse** (corso+docente, moduli, lezioni), nessun N+1.
- Nessuna scrittura: il servizio non esegue INSERT/UPDATE/DELETE/DDL.

## 3. Engine (`app/db/engine.py`)

```python
def create_engine(settings: Settings) -> AsyncEngine
```
- `create_async_engine(settings.database_url_async, pool_size=settings.db_pool_size (3),
  max_overflow=settings.db_max_overflow (4), pool_pre_ping=True, pool_recycle=1800,
  connect_args={"server_settings": {"default_transaction_read_only": "on",
  "statement_timeout": str(settings.db_statement_timeout_ms), "application_name": "a4u-lms-api"}})`.
- `settings.database_url_async`: proprietà di `Settings` che converte `postgresql://` e
  `postgres://` in `postgresql+asyncpg://` (lascia invariato `postgresql+asyncpg://`).
- La URL (contiene la password) non va mai nei log.

Dependency FastAPI per le connessioni (in `app/db/engine.py`):
```python
async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]
    # async with request.app.state.engine.connect() as conn: yield conn
```

## 4. Controllo del contratto (`app/db/contract.py`)

```python
EXPECTED_COLUMNS: dict[str, frozenset[str]]   # derivato da app.db.tables.metadata
class ContractError(Exception): ...           # messaggio: "colonne mancanti: course.cfu, users.email"
async def check_contract(conn: AsyncConnection) -> None   # solleva ContractError
```
- Query unica su `information_schema.columns` con `table_schema = 'public'` e
  `table_name IN (...)`. `information_schema` mostra solo le colonne su cui il ruolo ha un
  permesso: quindi il controllo verifica anche i GRANT (es. `users.email` senza GRANT = mancante).
- Messaggio di `ContractError`: tutte le colonne mancanti in forma `tabella.colonna`, ordinate.
- Colonne in più nel DB: ignorate.

## 5. Query (`app/db/queries.py`)

Tutte `async`, primo argomento `conn: AsyncConnection`. Restituiscono dataclass
`@dataclass(frozen=True, slots=True)` definite nello stesso modulo. Nessuna logica HTTP.

```python
@dataclass(frozen=True, slots=True)
class ApiKeyRow:
    id: UUID
    key_prefix: str
    key_hash: str
    expires_at: datetime | None

async def fetch_valid_api_key(conn, key_prefix: str) -> ApiKeyRow | None
    # filtra per key_prefix E validità (revoked/expires con now() del DB)

async def fetch_key_org_ids(conn, api_key_id: UUID) -> frozenset[UUID]
    # JOIN organizations, deleted_at IS NULL

@dataclass(frozen=True, slots=True)
class OrganizationRow:
    id: UUID; name: str; website: str | None; logo_path: str | None; courses_count: int

async def list_organizations(conn, org_ids: Collection[UUID]) -> list[OrganizationRow]
    # solo org in org_ids con deleted_at IS NULL; courses_count = tutti i corsi (ogni status);
    # ORDER BY name, id. org_ids vuoto -> [] senza query.

@dataclass(frozen=True, slots=True)
class DocenteRow:
    id: UUID; full_name: str; email: str

@dataclass(frozen=True, slots=True)
class CourseListRow:
    id: UUID; title: str; status: str; language_code: str; cfu: int
    corso_di_laurea: str | None; docente: DocenteRow
    created_at: datetime; updated_at: datetime; last_generated_at: datetime | None

async def list_courses(
    conn, org_id: UUID, *, status: str | None, updated_since: datetime | None,
    limit: int, offset: int,
) -> tuple[list[CourseListRow], int]          # (items, total)
    # JOIN organizations deleted_at IS NULL; filtro status = esatto;
    # filtro updated_since: GREATEST(c.updated_at, last_generated_at) >= :updated_since
    # (GREATEST ignora i NULL); ORDER BY c.updated_at DESC, c.id ASC; total = COUNT con gli
    # stessi filtri (seconda query o window function, a scelta).

@dataclass(frozen=True, slots=True)
class CourseDetailRow:
    # tutti i campi di CourseListRow più:
    objectives: str; argomenti_chiave: Any; course_overview: str | None

async def fetch_course(conn, org_id: UUID, course_id: UUID) -> CourseDetailRow | None
    # WHERE c.id = :course_id AND c.organization_id = :org_id AND org.deleted_at IS NULL

@dataclass(frozen=True, slots=True)
class ModuleRow:
    id: UUID; module_code: str; position: int; title: str; description: str

async def fetch_modules(conn, course_id: UUID) -> list[ModuleRow]     # ORDER BY position, id

@dataclass(frozen=True, slots=True)
class LessonRow:
    id: UUID; module_id: UUID; lesson_code: str; position: int; title: str; summary: str
    is_introductory: bool; is_assessment: bool
    learning_objectives: Any; mandatory_topics: Any          # jsonb grezzo
    content_status: str; content_raw: Any                    # None se non assessment/non pronta
    pdf_status: str; pdf_path: str | None; pdf_generated_at: datetime | None
    slides_pdf_status: str; slides_pdf_path: str | None; slides_pdf_generated_at: datetime | None
    speech_pdf_status: str; speech_pdf_path: str | None; speech_pdf_generated_at: datetime | None
    video_status: str; video_path: str | None; video_generated_at: datetime | None
    video_duration_raw: Any                                  # video_tokens -> 'video_duration_s'
    avatar_video_status: str; avatar_video_path: str | None
    avatar_video_generated_at: datetime | None

async def fetch_lessons(conn, course_id: UUID) -> list[LessonRow]
    # ORDER BY module_id, position, id (il router raggruppa per module_id)
```

### `last_generated_at` (in SQL, stessa espressione in `list_courses` e `fetch_course`)
Massimo, sulle lezioni del corso con `is_assessment = false`, di
`GREATEST(<x>_generated_at se <x>_status = 'ready' AND COALESCE(<x>_path, '') <> '')` per
x ∈ {pdf, slides_pdf, speech_pdf, video, avatar_video}. NULL se nessun file pronto.
Si calcola con una subquery aggregata o `LEFT JOIN LATERAL`; niente query per lezione.

## 6. Ruolo e permessi attesi

Produzione: ruolo `a4u_lms_ro` creato dal runbook §3 di `a4u/docs/lms-api.md`
(**al 05/11/2026 non esiste ancora in prod**). I test ricreano nel DB di test lo stesso ruolo
con gli stessi GRANT (vedi `modules.md` §4).
