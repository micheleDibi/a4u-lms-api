# Contratto: API HTTP v1

Proprietario: lead. Modifiche solo con richiesta `CONTRATTO:`.
Solo metodi GET. Risposte JSON con modelli Pydantic espliciti in `app/schemas.py`
(`response_model` su ogni route). Nessun CORS.

## 1. Autenticazione (tutti gli endpoint `/v1/*`)

Header `Authorization: Bearer <token>`.
`token = "a4u_lms_" + key_prefix + "_" + secret`, con `key_prefix` di 8 caratteri `[a-z0-9]`.
Il secret (43 caratteri `[A-Za-z0-9_-]`) **può contenere `_`**.

Lettura **per posizione**, mai con `split("_")`:
- `token.startswith("a4u_lms_")`;
- `key_prefix = token[8:16]`, che deve rispettare `^[a-z0-9]{8}$`;
- `token[16] == "_"`;
- `secret = token[17:]`, non vuoto;
- lunghezza totale ≤ 256 (protezione).

Flusso:
1. Lettura e controllo del formato; se fallisce → 401.
2. Si calcola `token_digest = sha256(token).hexdigest()`. Se è nella cache e non è scaduto → si usa il contesto in cache.
3. `fetch_valid_api_key(key_prefix)` → `hmac.compare_digest(token_digest, row.key_hash)`. Se fallisce → 401.
4. `fetch_key_org_ids(row.id)` → `AuthContext`, messo in cache.
5. Rate limit per `key_id` (§4).

**Cache:** chiave = `token_digest`, mai il token. Durata della voce = `min(now + AUTH_CACHE_SECONDS, expires_at della chiave)`. Si mettono in cache solo gli esiti positivi. Con `AUTH_CACHE_SECONDS=0` la cache è disattivata.

Conseguenza documentata: una revoca, o un cambio delle organizzazioni associate, ha effetto entro `AUTH_CACHE_SECONDS`.

Header mancante, schema diverso da `Bearer`, formato errato, chiave inesistente, revocata o scaduta → **401** con **lo stesso corpo**:
`{"detail": "Credenziali non valide o mancanti."}`, più l'header `WWW-Authenticate: Bearer`.

## 2. Errori

| status | quando | corpo |
|---|---|---|
| 401 | §1 | `{"detail": "Credenziali non valide o mancanti."}` + `WWW-Authenticate: Bearer` |
| 404 | org non associata alla chiave, soft-deleted o inesistente; corso inesistente o di un'org non accessibile; path sconosciuto | `{"detail": "Risorsa non trovata."}` |
| 422 | parametri di query o di path non validi (validazione FastAPI, formato di default) | default FastAPI |
| 405 | metodo diverso da GET (o HEAD dove FastAPI lo consente) su un path esistente | `{"detail": "Metodo non consentito."}` + header `Allow` invariato |
| 429 | rate limit | `{"detail": "Troppe richieste."}` + `Retry-After: <secondi interi ≥ 1>` |
| 503 | `/health/ready` non pronto | `{"status": "unavailable"}` |
| 500 | errore inatteso (loggato con traceback, senza header) | `{"detail": "Errore interno."}` |

**Mai 403.** Il 404 non distingue «non esiste» da «non accessibile».

## 3. Endpoint

### `GET /v1/organizations` → `list[OrganizationOut]`
```
OrganizationOut { id: UUID, name: str, website: str | null, logo_url: str | null, courses_count: int }
```
Restituisce le organizzazioni di `AuthContext.org_ids` con `deleted_at IS NULL`, ordinate per `name, id`. `logo_url` si costruisce come in `files.md` §4.

### `GET /v1/organizations/{org_id}/courses` → `CoursePage`
Query: `limit` (int, 1..100, default 50), `offset` (int ≥ 0, default 0), `status` (str, opzionale, max 40 caratteri, confronto esatto: uno status sconosciuto dà una lista vuota; uno status con caratteri non ammessi da Postgres, es. `\x00`, dà anch'esso una lista vuota senza interrogare il DB, mai 500), `updated_since` (datetime ISO 8601 **con fuso**; senza fuso → 422).

`org_id` non in `AuthContext.org_ids` → 404, senza toccare il DB.
```
CoursePage { items: list[CourseListItem], total: int, limit: int, offset: int }
CourseListItem {
  id: UUID, title: str, status: str, language_code: str, cfu: int,
  corso_di_laurea: str | null, docente: DocenteOut,
  created_at: datetime, updated_at: datetime, last_generated_at: datetime | null,
  last_assessment_at: datetime | null
}
DocenteOut { id: UUID, full_name: str, email: str }
```
Ordinamento `updated_at DESC, id ASC`. `updated_since` filtra su `GREATEST(updated_at, last_generated_at, last_assessment_at) >= updated_since`.
`last_generated_at` = ultimo file pronto delle lezioni non di verifica; `last_assessment_at` = ultima
rigenerazione, modifica o approvazione di un test di verifica pronto o approvato (db-read.md §5).

### `GET /v1/organizations/{org_id}/courses/{course_id}` → `CourseDetail`
Org non accessibile → 404. Corso inesistente o di un'altra org → 404.
```
CourseDetail {
  id, title, status, language_code, cfu, corso_di_laurea, created_at, updated_at, last_generated_at,
  last_assessment_at,
  docente: DocenteOut,
  obiettivi_formativi: str,            # course.objectives
  argomenti_chiave: list[str],         # elementi non stringa scartati (warning)
  course_overview: str | null,
  modules: list[ModuleOut]             # per position
}
ModuleOut { id: UUID, module_code: str, position: int, title: str, description: str,
            lessons: list[LessonOut] }  # per position
LessonOut {
  id: UUID, lesson_code: str, position: int, title: str, summary: str,
  is_introductory: bool, is_assessment: bool,
  learning_objectives: list[str],                 # elementi non stringa scartati
  mandatory_topics: list[MandatoryTopicOut],      # elementi senza topic_id/topic stringa scartati
  files: LessonFiles,
  test: AssessmentTest | null
}
MandatoryTopicOut { topic_id: str, topic: str }
LessonFiles { dispensa: FileOut | null, slide: FileOut | null, discorso: FileOut | null,
              video: VideoFileOut | null, video_avatar: FileOut | null }
FileOut { url: str, generated_at: datetime | null }
VideoFileOut { url: str, generated_at: datetime | null, duration_s: float | null }
AssessmentTest {
  approved: bool,
  multiple_choice_questions: list[MCQuestionOut],
  open_questions: list[OpenQuestionOut]
}
MCQuestionOut { question_id: str, text: str, options: list[MCOptionOut], correct_option_id: str }
MCOptionOut { option_id: str, text: str }
OpenQuestionOut { question_id: str, text: str, expected_answer: str }
```
Le regole di `files` e `test` sono in `files.md`. I moduli senza lezioni hanno `lessons: []`. Le lezioni il cui `module_id` non appartiene ai moduli del corso vengono ignorate.

### `GET /health/live` → `{"status": "ok"}` (200, nessuna autenticazione, nessun accesso al DB)

### `GET /health/ready` (nessuna autenticazione)
- Esegue `SELECT 1`, più `check_contract` con una cache in memoria di 30 s (solo l'esito positivo va in cache).
- Se tutto va bene → 200 `{"status": "ok"}`. Altrimenti → 503 `{"status": "unavailable"}`: il dettaglio va **solo nel log**, non nella risposta.

### OpenAPI
`/docs`, `/redoc` e `/openapi.json` esistono solo con `DOCS_ENABLED=true`; altrimenti → 404.

## 4. Rate limit (`app/ratelimit.py`)
- Token bucket in memoria per `key_id`: capacità `RATE_LIMIT_PER_MIN`, ricarica di `RATE_LIMIT_PER_MIN / 60` gettoni al secondo. Ogni richiesta `/v1` autenticata consuma 1 gettone.
- Senza gettoni → 429 con `Retry-After = ceil(tempo per 1 gettone)`.
- Valido con un solo processo uvicorn.
- `/health/*` non ha limite.

## 5. Log di accesso
Una riga JSON per richiesta (middleware), `event = "access"`, con i campi `key_prefix` (o `null`), `method`, `path` (senza query string), `status`, `duration_ms`.

Il token, l'header `Authorization` e la query string non vanno mai nei log. Il log di accesso di uvicorn è disattivato (`--no-access-log`).
