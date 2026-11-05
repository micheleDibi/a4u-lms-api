# Guida all'integrazione per gli sviluppatori dell'LMS

Questa guida spiega come un LMS legge da a4u corsi, moduli, lezioni, file e test di verifica
tramite **a4u-lms-api**.

- **URL base:** `https://lms-api.ersaf.it`
- **Esempi:** `docs/esempi/`. Sono risposte reali con il docente anonimizzato. Il test di
  verifica e i percorsi degli URL dei file sono fittizi, quindi quegli URL non scaricano nulla.
- **Specifica OpenAPI:** `docs/openapi.json`, da importare in Postman, Insomnia o Swagger
  Editor per avere tutte le richieste già pronte.

## 1. Panoramica

- **Sola lettura:** l'API accetta solo `GET`. Gli altri metodi ricevono 405. L'LMS non può
  modificare nulla in a4u.
- **Cosa espone:**
  - le organizzazioni associate alla chiave;
  - i corsi di ogni organizzazione;
  - per ogni corso, i moduli e le lezioni, gli URL dei file (dispensa, slide, discorso, video,
    video con avatar) e i test di verifica, soluzioni comprese.
- **Versione:** tutti gli endpoint stanno sotto `/v1`. In futuro possono comparire nuovi
  campi nelle risposte: il client deve ignorare quelli che non conosce.
- **Formato:** JSON in UTF-8 (`Content-Type: application/json`), solo su HTTPS.
- **Date:** ISO 8601 in UTC con suffisso `Z`, per esempio `2026-11-05T12:31:36.219129Z`. La
  parte frazionaria dei secondi è facoltativa: ha fino a 6 cifre e manca se vale zero
  (`2026-11-05T12:31:36Z`). Serve un parser ISO 8601 completo, non un formato fisso.
- **Identificativi:** UUID stabili. Sono la chiave da usare nell'LMS per riconoscere le entità
  tra una sincronizzazione e l'altra.

## 2. Autenticazione

Ogni richiesta a `/v1` porta l'header:

```
Authorization: Bearer a4u_lms_<key_prefix>_<secret>
```

- **Formato del token:** `a4u_lms_`, poi 8 caratteri `[a-z0-9]` (il `key_prefix`), poi `_`,
  poi un secret di 43 caratteri `[A-Za-z0-9_-]`. Il secret **può contenere `_`**, quindi il
  token va trattato come una stringa opaca, senza dividerlo.
- **Dove si crea:** l'amministratore di a4u crea la chiave (*Admin → Chiavi API LMS*) e la
  associa a una o più organizzazioni. Il token compare **una sola volta**: va conservato in un
  secret store dell'LMS. Se si perde non si recupera: si crea una nuova chiave.
- **Dove non va mai:**
  - nella query string;
  - nei log dell'LMS (attenzione ai log delle richieste HTTP dei client);
  - nel codice frontend o nel browser.

  Le chiamate partono solo dal backend dell'LMS.
- **401:** header mancante, schema diverso da `Bearer`, token malformato, chiave inesistente,
  revocata o scaduta danno **sempre la stessa risposta**: 401
  `{"detail": "Credenziali non valide o mancanti."}` con `WWW-Authenticate: Bearer`. Il
  servizio non distingue i casi di proposito.
- **Revoca:** ha effetto **entro 30 secondi**. Lo stesso vale per un cambio delle
  organizzazioni associate alla chiave.
- **Rotazione:**
  1. l'amministratore crea una nuova chiave con le stesse organizzazioni;
  2. l'LMS passa al nuovo token e verifica `GET /v1/organizations`;
  3. solo dopo l'amministratore revoca la vecchia chiave.

## 3. Limiti

- **120 richieste al minuto per chiave**, come token bucket: si possono fare brevi raffiche
  fino a 120 richieste, poi si recupera una richiesta ogni 0,5 s.
- **Oltre il limite** arriva 429 `{"detail": "Troppe richieste."}` con l'header `Retry-After`,
  in secondi interi ≥ 1. Il client deve **attendere almeno `Retry-After` secondi** prima di
  riprovare, e poi applicare un backoff esponenziale con jitter se il 429 si ripete.
- **Limite per indirizzo IP:** l'infrastruttura può applicare anche un limite per indirizzo
  IP. In quel caso il 429 arriva **senza** `Retry-After` e senza corpo JSON: si attende almeno
  1 secondo prima di riprovare.
- **In pratica:** una sincronizzazione sequenziale, con al massimo 2 richieste al secondo,
  resta sotto i limiti. Non servono richieste in parallelo.

## 4. Endpoint

In tutti gli esempi `$TOKEN` è un segnaposto per il token della propria chiave.

### 4.1 `GET /v1/organizations`

Restituisce le organizzazioni associate alla chiave, ordinate per nome. Non ha parametri.

```sh
curl -s -H "Authorization: Bearer $TOKEN" https://lms-api.ersaf.it/v1/organizations
```

Risposta: un array di oggetti. Esempio: `docs/esempi/organizations.json`.

| campo | tipo | note |
|---|---|---|
| `id` | UUID | identificativo dell'organizzazione |
| `name` | string | |
| `website` | string \| null | così come salvato in a4u: **da validare** (§9) |
| `logo_url` | string \| null | URL del logo, di solito su OVH; può essere un URL assoluto di un altro host o un `data:`: **da validare** (§9) |
| `courses_count` | int | numero di corsi dell'organizzazione, in qualsiasi status |

### 4.2 `GET /v1/organizations/{org_id}/courses`

Elenco paginato dei corsi di un'organizzazione, senza moduli e lezioni.

| parametro | tipo | default | note |
|---|---|---|---|
| `limit` | int | 50 | da 1 a 100; oltre 100 la risposta è 422 |
| `offset` | int | 0 | ≥ 0 |
| `status` | string | — | confronto esatto con lo status del corso; uno status sconosciuto dà una lista vuota |
| `updated_since` | datetime | — | ISO 8601 **con fuso orario**, altrimenti 422; usare `Z`, oppure codificare il `+` come `%2B` (vedi §7) |

Un'organizzazione non associata alla chiave dà 404, come una inesistente.

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://lms-api.ersaf.it/v1/organizations/<org_id>/courses?limit=100&offset=0"

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://lms-api.ersaf.it/v1/organizations/<org_id>/courses?status=published"

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://lms-api.ersaf.it/v1/organizations/<org_id>/courses?updated_since=2026-11-01T00:00:00Z"
```

Risposta: `{ "items": [...], "total": int, "limit": int, "offset": int }`. Esempi:
`docs/esempi/courses_list.json`, `docs/esempi/courses_updated_since.json`.

- **Ordinamento:** `updated_at` decrescente, poi `id` crescente.
- **`total`:** numero complessivo dei corsi che rispettano i filtri, non solo quelli della
  pagina.

| campo di `items[]` | tipo | note |
|---|---|---|
| `id` | UUID | |
| `title` | string | |
| `status` | string | stato di avanzamento in a4u, vedi sotto |
| `language_code` | string | per esempio `it` |
| `cfu` | int | |
| `corso_di_laurea` | string \| null | |
| `docente` | object | `{ id: UUID, full_name: string, email: string }` |
| `created_at` | datetime | |
| `updated_at` | datetime | ultima modifica del corso in a4u |
| `last_generated_at` | datetime \| null | ultimo file pronto tra le lezioni normali; null se nessuno |
| `last_assessment_at` | datetime \| null | ultima rigenerazione, modifica o approvazione di un test di verifica esposto; null se nessuno |

**Valori di `status`**, nell'ordine del flusso di a4u:

1. `draft`;
2. `architecture_pending`, `architecture_ready`, `architecture_approved`;
3. `lessons_structure_pending`, `lessons_structure_ready`, `lessons_structure_approved`;
4. `content_pending`, `content_ready`, `content_approved`;
5. `slides_pending`, `slides_ready`, `slides_approved`;
6. `speech_pending`, `speech_ready`, `speech_approved`;
7. `video_pending`, `video_ready`;
8. `avatar_video_pending`, `avatar_video_ready`;
9. `published`, `archived`.

Lo status descrive l'avanzamento complessivo del corso. Per sapere quali file sono già
disponibili conviene guardare i singoli file (§5), non lo status.

### 4.3 `GET /v1/organizations/{org_id}/courses/{course_id}`

Il corso completo: moduli, lezioni, file e test. Un corso inesistente, o di un'altra
organizzazione, dà 404.

```sh
curl -s -H "Authorization: Bearer $TOKEN" \
  "https://lms-api.ersaf.it/v1/organizations/<org_id>/courses/<course_id>"
```

Esempio: `docs/esempi/course_detail.json`.
- È **ridotto** a 1 modulo, con una lezione normale e una lezione di verifica. Il corso reale
  ha 6 moduli e 48 lezioni, circa 240 KB di JSON compatto.
- Il **test** della lezione di verifica è **fittizio**: ha la struttura reale, ma domande e
  soluzioni sono inventate.

Il dettaglio contiene tutti i campi dell'elenco, più:

| campo | tipo | note |
|---|---|---|
| `obiettivi_formativi` | string | testo libero, anche su più paragrafi (`\n\n`) |
| `argomenti_chiave` | string[] | |
| `course_overview` | string \| null | |
| `modules` | object[] | ordinati per `position` |

Modulo (`modules[]`):

| campo | tipo | note |
|---|---|---|
| `id` | UUID | |
| `module_code` | string | per esempio `M1` |
| `position` | int | ordine nel corso |
| `title`, `description` | string | |
| `lessons` | object[] | ordinate per `position`; `[]` se il modulo non ha lezioni |

Lezione (`lessons[]`):

| campo | tipo | note |
|---|---|---|
| `id` | UUID | |
| `lesson_code` | string | per esempio `M1.L1` |
| `position` | int | ordine nel modulo |
| `title`, `summary` | string | |
| `is_introductory` | bool | lezione introduttiva del corso |
| `is_assessment` | bool | lezione di verifica: ha `test` e nessun file |
| `learning_objectives` | string[] | |
| `mandatory_topics` | object[] | `{ topic_id: string, topic: string }` |
| `files` | object | 5 voci, ciascuna oggetto oppure `null` (§5) |
| `test` | object \| null | solo per le lezioni di verifica pronte o approvate (§6) |

## 5. File delle lezioni

`files` ha sempre queste 5 voci:

| voce | contenuto | campi |
|---|---|---|
| `dispensa` | PDF della dispensa | `url`, `generated_at` |
| `slide` | PDF delle slide | `url`, `generated_at` |
| `discorso` | PDF del testo parlato | `url`, `generated_at` |
| `video` | video MP4 della lezione | `url`, `generated_at`, `duration_s` (secondi, float o null) |
| `video_avatar` | video MP4 con avatar | `url`, `generated_at` |

- **`null`:** il file non è pronto o non esiste. Può diventare disponibile in una
  sincronizzazione successiva.
- **Hosting:** gli URL sono **pubblici** su OVH (`https://progettiersaf.com/media/...`): si
  scaricano o si incorporano senza token. Non vanno mai chiamati con l'header `Authorization`
  di questa API.
- **Cache:** quando la data di generazione del file (`generated_at`) è valorizzata, l'URL
  termina con `?v=<epoch>`, quell'istante in secondi, che cambia a ogni rigenerazione. Se
  `generated_at` è `null`, l'URL non ha `?v=`. La chiave di cache è sempre l'**URL completo**,
  `?v=` compreso se c'è: se l'URL cambia, il file è nuovo e va riscaricato, oppure la cache va
  invalidata.
- **Lezioni di verifica:** (`is_assessment = true`) hanno tutte e 5 le voci a `null`.
- **Possibili 404:** pochi file molto vecchi potrebbero non essere presenti su OVH, e il loro
  URL risponde 404. Se capita, va segnalato al referente di a4u indicando corso, lezione e
  voce. Non si ritenta in automatico.

## 6. Test di verifica

`test` è presente solo nelle lezioni di verifica con il contenuto pronto o approvato.
Altrimenti è `null`.

```
test {
  approved: bool,                        // true = test approvato in a4u; false = pronto ma non ancora approvato
  multiple_choice_questions: [
    { question_id: string, text: string,
      options: [ { option_id: string, text: string }, ... ],   // almeno 2, option_id unici
      correct_option_id: string }                              // uno degli option_id
  ],
  open_questions: [
    { question_id: string, text: string, expected_answer: string }
  ]
}
```

- **Ordine:** domande e opzioni sono nell'ordine originale. Le domande malformate in a4u
  vengono escluse dal servizio, quindi tutto ciò che arriva è completo.
- **`approved`:** conviene pubblicare agli studenti solo i test con `approved = true`. Un test
  con `false` può ancora cambiare.
- **Soluzioni:** **le soluzioni sono incluse** (`correct_option_id`, `expected_answer`) e **non
  devono mai arrivare al browser dello studente**. L'LMS le salva lato server, manda al client
  solo `question_id`, `text` e `options`, e corregge lato server. `expected_answer` è una
  traccia per chi corregge, non un testo da confrontare alla lettera.
- **Riconoscere le domande:** usare `question_id` (`MC1`, `OP1`, ...) all'interno della
  lezione. Dopo una rigenerazione del test, gli stessi `question_id` possono indicare domande
  diverse: si sostituisce l'intero test della lezione.

## 7. Sincronizzazione consigliata

**1. Caricamento iniziale**
- Chiamare `GET /v1/organizations`, poi, per ogni organizzazione, scorrere
  `/courses?limit=100&offset=0,100,200,...` finché `offset + len(items) >= total`.
- Per ogni corso chiamare il dettaglio.
- Salvare tutto **per `id`**, con un upsert idempotente: rileggere lo stesso corso non deve
  creare duplicati.
- L'ordinamento è per `updated_at` decrescente: se un corso cambia durante la scansione, una
  pagina può contenere un corso già visto. Si deduplica per `id`, e il corso cambiato verrà
  comunque ripreso dalla sincronizzazione incrementale successiva.

**2. Sincronizzazione incrementale**, per esempio ogni 15-60 minuti:
1. Prima di iniziare, salvare l'istante corrente `T` in UTC.
2. Chiamare `/courses?updated_since=<T_precedente - margine>` (margine consigliato: 5 minuti,
   contro differenze di orologio e transazioni in corso) e scorrere tutte le pagine.
3. Per ogni corso restituito, rileggere il dettaglio e sostituire nell'LMS moduli, lezioni,
   file e test.
4. A fine giro riuscito, memorizzare `T` come nuovo `T_precedente`.

Il filtro considera il più recente di tre istanti, quindi un corso rientra se è cambiato in
uno qualsiasi dei tre:
- `updated_at`: modifiche al corso;
- `last_generated_at`: un nuovo file pronto;
- `last_assessment_at`: un test rigenerato, modificato o approvato.

Il formato di `updated_since` deve avere il fuso: `2026-11-05T14:00:00Z`. Un `+01:00` nella
query string va scritto `%2B01:00`, perché un `+` non codificato diventa uno spazio e la
risposta è 422.

**3. Riallineamento completo periodico**, per esempio una volta al giorno: rifare il
caricamento iniziale, senza `updated_since`, e confrontare con i dati dell'LMS. Serve a
rilevare ciò che il filtro incrementale non segnala:
- corsi eliminati in a4u, o organizzazioni non più associate alla chiave: non compaiono più
  (oppure danno 404);
- test che non sono più esposti, per una rigenerazione in corso o fallita oppure per una
  lezione di verifica eliminata: il test diventa `null` o sparisce senza che nessuna data
  avanzi;
- file tornati a `null`.

Cosa fare dei corsi spariti (archiviarli, nasconderli) è una scelta dell'LMS. Conviene non
cancellarli subito: un 404 temporaneo è possibile, per esempio dopo un cambio delle
organizzazioni associate alla chiave.

## 8. Errori

| status | quando | corpo | header | cosa fare |
|---|---|---|---|---|
| 401 | token mancante, malformato, inesistente, revocato o scaduto | `{"detail": "Credenziali non valide o mancanti."}` | `WWW-Authenticate: Bearer` | non ritentare: verificare il token, poi chiedere una nuova chiave |
| 404 | organizzazione non associata alla chiave o inesistente; corso inesistente o di un'altra organizzazione; path sconosciuto | `{"detail": "Risorsa non trovata."}` | — | non ritentare; nel riallineamento conta come «non più disponibile» |
| 405 | metodo diverso da GET | `{"detail": "Metodo non consentito."}` | `Allow: GET` | usare GET |
| 422 | parametro non valido (`limit` > 100, `updated_since` senza fuso, UUID malformato, ...) | `{"detail": [{"type", "loc", "msg", "input", ...}]}` (formato standard di FastAPI) | — | correggere la richiesta: `loc` indica il parametro |
| 429 | limite di richieste superato | `{"detail": "Troppe richieste."}` | `Retry-After: <secondi>` | attendere `Retry-After` (o almeno 1 s se manca), poi backoff |
| 500 | errore inatteso del servizio | `{"detail": "Errore interno."}` | — | ritentare più tardi con backoff; se persiste, segnalarlo con data e ora |
| 502 / 503 / 504 | servizio non raggiungibile o in riavvio | variabile | — | ritentare più tardi con backoff |

Non esiste il 403: una risorsa non accessibile risponde 404, come una inesistente.

Esempi reali delle risposte d'errore: `docs/esempi/errori.json`.

## 9. Sicurezza lato LMS

- **Testi come testo:** tutti i testi (titoli, descrizioni, obiettivi, riassunti, domande,
  opzioni, risposte attese, nomi) sono testo semplice generato o modificato in a4u. Vanno
  mostrati **con escape HTML**, con `textContent` o con l'escape automatico del template
  engine. Non vanno mai inseriti con `innerHTML`, `v-html`, `dangerouslySetInnerHTML` o
  simili.
- **`website` e `logo_url`:** arrivano **così come sono salvati in a4u**. Prima di usarli in un
  `href` o in un `src` vanno validati:
  - `website`: accettare solo `https://` e `http://`; scartare tutto il resto, per esempio
    `javascript:` o `data:`;
  - `logo_url`: accettare solo `https://`, `http://` e
    `data:image/(png|jpeg|gif|webp);base64,...`; scartare il resto (in particolare
    `data:image/svg+xml` e `data:text/html`).
- **Token e soluzioni:** il token resta sul backend (§2) e le soluzioni dei test non arrivano
  mai al browser (§6).

## 10. FAQ

**Come trovo l'`org_id`?**
Con `GET /v1/organizations`: restituisce solo le organizzazioni associate alla propria chiave.

**Ricevo 404 su un'organizzazione che esiste.**
La chiave non è associata a quell'organizzazione, oppure l'organizzazione è stata eliminata
in a4u. L'associazione la gestisce l'amministratore di a4u. Dopo una modifica serve fino a
30 secondi.

**Perché un corso ha `files` tutti `null`?**
I file non sono ancora stati generati, oppure è una lezione di verifica. Si ricontrolla alla
sincronizzazione successiva.

**Il link di un file è cambiato: devo riscaricarlo?**
Sì. Un `?v=` diverso significa che il file è stato rigenerato.

**Un URL di file risponde 404.**
Va segnalato al referente di a4u (§5). L'API non ha modo di saperlo in anticipo.

**`updated_since` mi restituisce 422.**
Manca il fuso orario, oppure il `+` del fuso non è codificato. Si usa `Z`:
`?updated_since=2026-11-05T14:00:00Z`.

**Un test è sparito, ma `updated_since` non me l'ha segnalato.**
È il limite descritto al §7. Il riallineamento completo periodico lo rileva, e il corso torna
nel filtro incrementale quando il test viene rigenerato.

**Posso chiamare l'API dal browser?**
No: il token sarebbe esposto e l'API non abilita il CORS. Le chiamate partono solo dal
backend dell'LMS.

**Posso usare più chiavi o più processi in parallelo?**
Il limite è di 120 richieste al minuto per chiave. Una sincronizzazione sequenziale basta
quasi sempre. Più chiavi servono solo per separare ambienti diversi (per esempio test e
produzione), non per aumentare il limite.

**Gli endpoint `/health/live` e `/health/ready`?**
Servono al monitoraggio interno e possono non essere raggiungibili dall'esterno. L'LMS non
deve dipenderne. Per controllare che il token funzioni si usa `GET /v1/organizations`.
