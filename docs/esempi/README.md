# Esempi di risposte dell'API

Risposte **reali** di `https://lms-api.ersaf.it`, scaricate dalla produzione il 05/11/2026 e
poi anonimizzate. Fanno eccezione due parti fittizie:
- il test della lezione di verifica;
- i percorsi degli URL dei file.

Gli esempi sono formattati con indentazione di 2 spazi; il servizio risponde
in JSON compatto.

| file | richiesta |
|---|---|
| `organizations.json` | `GET /v1/organizations` |
| `courses_list.json` | `GET /v1/organizations/{org_id}/courses?limit=50&offset=0` |
| `courses_updated_since.json` | `GET /v1/organizations/{org_id}/courses?updated_since=2026-11-01T00:00:00Z` |
| `course_detail.json` | `GET /v1/organizations/{org_id}/courses/{course_id}`, **ridotto** (vedi sotto) |
| `errori.json` | risposte d'errore, vedi la tabella sotto |

Richieste che hanno prodotto le risposte di `errori.json`:

| voce | richiesta |
|---|---|
| `401_credenziali_mancanti` | `GET /v1/organizations` senza header `Authorization` |
| `404_organizzazione` | `GET /v1/organizations/00000000-0000-0000-0000-000000000000/courses` |
| `404_corso` | `GET /v1/organizations/{org_id}/courses/00000000-0000-0000-0000-000000000000`, con un'organizzazione reale |
| `405_metodo` | `POST /v1/organizations` |
| `422_limit` | `GET /v1/organizations/{org_id}/courses?limit=500` |
| `422_updated_since_senza_fuso` | `GET /v1/organizations/{org_id}/courses?updated_since=2026-11-01T00:00:00` |
| `429_troppe_richieste` | ricostruita dal contratto, non catturata |

## Cosa è stato modificato

- **Docenti:** `docente.full_name` e `docente.email` sono fittizi (`Mario Rossi`,
  `mario.rossi@example.org`). A uno stesso `docente.id` corrisponde sempre lo stesso nome
  fittizio. L'`id` è quello reale.
- **Testi:** non c'erano altri nomi di persone private né email (riassunti, obiettivi,
  domande, risposte attese). I nomi di scienziati citati come contenuto didattico (Newton,
  Ampère, ...) sono rimasti.
- **Dati reali:** UUID dei campi `id`, nomi delle organizzazioni, titoli e contenuti dei corsi,
  URL dei loghi.
- **URL dei file:** in `course_detail.json` gli UUID nei **percorsi** degli URL sono
  fittizi: `11111111-…` per l'organizzazione, `22222222-…` per il corso, `33333333-…` per la
  lezione. Restano reali il dominio, la struttura dei percorsi (`generated_pdfs/…`,
  `uploads/lesson_videos/…`, `uploads/lesson_avatar_videos/…`) e `?v=`. Gli URL degli
  esempi quindi non scaricano nulla. Nell'API reale i percorsi contengono gli `id` veri.
- **`course_detail.json`:** contiene solo il primo modulo (il corso reale ne ha 6, con 48
  lezioni), con due lezioni:
  - una lezione normale con tutti e 5 i file (`M1.L1`);
  - la lezione di verifica `M1.L8`, con 2 domande a scelta multipla e 2 domande aperte (in
    origine 30 e 6).

  Il corso e la lezione normale sono dati reali, anonimizzati solo nel docente.
- **Test fittizio:** il **test della lezione di verifica è fittizio**. Testi delle domande,
  opzioni ed `expected_answer` sono stati riscritti con contenuti di fisica di base, per non
  pubblicare un test reale con le sue soluzioni. La struttura è quella reale: `question_id`,
  `option_id`, `correct_option_id`, `approved` e numero di opzioni.
- **`errori.json`:** ogni voce ha `status`, `richiesta` (descrizione della chiamata),
  `header_rilevanti` e `body`.
  - I body e gli header sono quelli reali.
  - Degli header sono riportati solo quelli utili all'integratore: `WWW-Authenticate`,
    `Allow` e `Retry-After`. Tutte le risposte hanno `Content-Type: application/json`.
    Gli header dell'infrastruttura sono stati omessi.
  - La **429** non è stata catturata in produzione: è ricostruita dal contratto
    (`docs/contracts/api-v1.md` §2 e §4).
- **Assenti:** token, header `Authorization` e password.

Le soluzioni dei test (`correct_option_id`, `expected_answer`) sono presenti nella stessa forma
dell'API reale. Nell'LMS non vanno mai mandate al browser dello studente.
