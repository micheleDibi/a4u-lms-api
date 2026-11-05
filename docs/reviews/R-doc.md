# Revisione R-doc: privacy e coerenza della documentazione per l'LMS (task #49)

Revisore: reviewer · Riferimenti: `docs/contracts/api-v1.md`, `docs/contracts/files.md`, `app/schemas.py`, risposte reali in `prod-raw` (scratchpad del lead)
File: `docs/esempi/**`, `docs/integrazione-lms.md`, `docs/openapi.json`, diff di `README.md`, diff di `app/` (solo metadati OpenAPI)

## Esito: OK (nessun P0/P1, cinque P2)

Perimetro della privacy deciso dal piano: si anonimizzano solo le persone (nome ed email), e non devono comparire token né dettagli di infrastruttura. UUID, nomi delle organizzazioni, titoli e contenuti dei corsi e URL dei file restano reali e non sono rilievi.

## 1. Privacy (bloccante): OK
- **Persone:** dalle risposte reali ho estratto nomi completi, singole parole dei nomi, email e parte locale delle email dei docenti, per un totale di 5 stringhe tenute solo nello scratchpad del revisore. La ricerca case-insensitive in `docs/esempi/`, `docs/integrazione-lms.md`, `docs/openapi.json`, `README.md`, `app/` e `tests/unit/test_unit_openapi.py` dà **0 occorrenze** per ciascuna.
- **Sostituzione coerente:** in tutti gli esempi c'è un solo `docente.id`, sempre con lo stesso valore fittizio (`Mario Rossi`, `mario.rossi@example.org`), come dichiarato in `docs/esempi/README.md`.
- **Confronto campo per campo con `prod-raw`:** `organizations.json` è identico al reale. `courses_list.json`, `courses_updated_since.json` e `course_detail_completo.json` differiscono dal reale **solo** in `docente.full_name` e `docente.email`.
- **Token, credenziali, infrastruttura:** nessun token `a4u_lms_<prefix>_<secret>`, nessun `Authorization` valorizzato (la guida usa solo `$TOKEN`), nessuna password e nessun IP `192.168.*` o `10.*`. Non compaiono neppure `X-Served-By`, `Server`, openresty o il nome della VM in guida, esempi e `openapi.json`. In `errori.json` sono riportati solo `WWW-Authenticate`, `Allow` e `Retry-After`.
- **Diff di `README.md`:** contiene IP interni, la regola iptables, l'unità systemd e la configurazione NPM. Il README però è il documento per l'operatore e conteneva già questi dettagli in `HEAD`. Non è materiale per l'LMS: vedi P2-5.

## 2. Coerenza con contratto e schemi: OK
- **Validazione con `app/schemas.py`:** `organizations.json` (`list[OrganizationOut]`), i due `CoursePage` e i due `CourseDetail` sono validi, e il **round-trip** (validazione e poi `dump_json`) ridà esattamente l'esempio: nessun campo in più, in meno o con un tipo diverso.
- **`course_detail.json` ridotto:** tutti i campi del corso e i metadati del modulo 1 sono identici al completo. `M1.L1` è identica. In `M1.L8`, `multiple_choice_questions` e `open_questions` sono esattamente le prime 2 di 30 e di 6. Corrisponde a quanto dichiarato.
- **`errori.json`:** per 401, 404 org, 404 corso, 405 e i due 422, status, body e header coincidono con le catture reali. Il 429 è dichiarato come ricostruito e coincide con il contratto (`{"detail": "Troppe richieste."}` più `Retry-After`).
- **Guida contro `api-v1.md` e `files.md`:**
  - campi e tipi delle 4 risorse coincidono, compreso `last_assessment_at`;
  - parametri e limiti coincidono: `limit` 1..100 con default 50, `offset` ≥ 0, `status` esatto, `updated_since` con fuso (422 se manca), `%2B`;
  - messaggi d'errore identici al contratto, compreso il 405 `Metodo non consentito.` con `Allow`;
  - ordinamenti, 404 mai 403, cache dell'autenticazione di 30 s e limite di 120 al minuto (i default di `config.md`), regole di file e test, limite noto di `last_assessment_at` e consiglio di riallineamento periodico (db-read §5);
  - `question_id` `MC1`/`OP1` coerenti con gli esempi.
- **`docs/openapi.json`:**
  - **identico** allo schema generato (`openapi_schema()`) e all'output di `python -m app.cli export-openapi`;
  - limiti dei parametri, `required` e nullabilità di tutti i modelli coerenti con il contratto;
  - 401, 429 e 500 documentati con i messaggi esatti sulle route `/v1`, 404 sulle route con `org_id`, 503 su `/health/ready`;
  - `bearerAuth` solo sulle operazioni `/v1`;
  - `servers` contiene solo l'URL pubblico.

## 3. Comportamento HTTP invariato: OK
- **Schemi:** confronto dei JSON schema di tutti i 15 modelli di `app/schemas.py`, prima (`HEAD`) e dopo, togliendo `description`, `title` ed `examples`: **nessuna differenza**. Le uniche aggiunte sono `ErrorOut` e `HealthOut`, usati solo per la documentazione.
- **Route:** il diff aggiunge solo `summary`, `description`, `responses` (documentazione) e `Path(description=…)`. Non cambiano validatori, default, status code o handler.
- **Health:** `/health/ready` restituisce ancora `JSONResponse`, quindi il `response_model` non ha effetto. `/health/live` restituisce `{"status": "ok"}`, valido per `HealthOut`.
- **OpenAPI:** `app.openapi` è sostituito, ma `openapi_url` resta `None` con `DOCS_ENABLED=false` (coperto dai test core).
- **Suite completa** (`TEST_DB_NAME=a4u_lms_test_reviewer uv run --frozen pytest`): tutto verde, compresi i test core su 401, 404, 405, 500, docs e health. `mypy app`, `ruff check` e `ruff format --check`: verdi.

## Problemi
| # | file:riga | gravità | problema | correzione proposta |
|---|---|---|---|---|
| 1 | `docs/integrazione-lms.md:23-24` | P2 | «ISO 8601 in UTC con suffisso `Z`, **con i microsecondi**». Pydantic omette la parte frazionaria quando i microsecondi sono 0 (verificato: `2026-01-01T00:00:00Z`). Un parser dell'LMS rigido sul formato con i microsecondi fallirebbe su queste date. | «…con suffisso `Z`; la parte frazionaria (fino ai microsecondi) può mancare: usare un parser ISO 8601 standard». |
| 2 | `docs/integrazione-lms.md:221-224` (§5, *Cache*) | P2 | «Ogni URL termina con `?v=<epoch>`». Per `files.md` §3, `?v=` c'è **solo** se `generated_at` non è `null`. Negli esempi reali tutti i 169 URL lo hanno, ma il caso senza è previsto e testato (`video_avatar` con `generated_at` nullo). | «Se `generated_at` è valorizzato, l'URL termina con `?v=<epoch>`…; se è `null`, il parametro manca». |
| 3 | `docs/integrazione-lms.md:66-68` | P2 | La guida dà per attivo un limite per IP di circa 10 richieste al secondo, ma il README (diff, §5) dice che il blocco `limit_req` è «**da riapplicare**», quindi oggi non è attivo. Il comportamento consigliato al client (attendere 1 s sul 429 senza `Retry-After`) resta corretto. | «…può esserci anche un limite per IP nell'infrastruttura…», oppure riapplicare il blocco NPM prima di consegnare la guida. |
| 4 | `docs/openapi.json` (paths `/v1/*`) | P2 | Il 405 (`Metodo non consentito.` più `Allow`) è nel contratto e nella guida, ma non nello schema OpenAPI. Le route dichiarano solo `get`, quindi un client generato non lo produce mai: va documentato solo per completezza. | Facoltativo: menzionarlo in `API_DESCRIPTION` (per esempio «solo GET; altri metodi → 405»). |
| 5 | `README.md` (diff, §4-§5) | P2 (nota) | Il README contiene IP interni, la regola del firewall e la configurazione del proxy, ed è ora collegato dalla sezione «Per l'integratore LMS». Va bene per un repo interno, ma **non** va consegnato all'LMS insieme alla guida. | All'LMS consegnare solo `docs/integrazione-lms.md`, `docs/esempi/` e `docs/openapi.json` (sono autosufficienti). Eventualmente scriverlo in testa a `docs/integrazione-lms.md` o nel messaggio di consegna. |

## Note
- Le soluzioni dei test sono presenti negli esempi, come nell'API reale, con l'avvertenza esplicita in `docs/esempi/README.md` e nella guida §6.
- In §1 la guida dice «gli altri metodi ricevono 405». `HEAD` su una route GET risponde 200 senza corpo (Starlette), e il contratto lo prevede («o HEAD dove FastAPI lo consente»). È irrilevante per l'integratore.
