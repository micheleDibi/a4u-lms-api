# a4u-lms-api

Servizio HTTP di **sola lettura** che espone a un LMS esterno i corsi generati da a4u:
organizzazioni, corsi, moduli, lezioni, URL dei file su OVH e test di verifica.
Legge il Postgres di a4u con il ruolo `a4u_lms_ro` e non scrive nulla: niente migrazioni,
niente INSERT/UPDATE/DELETE, solo metodi `GET`.

Specifica completa in `docs/contracts/` (`api-v1.md`, `files.md`, `db-read.md`, `config.md`,
`modules.md`). La fonte a monte è `a4u/docs/lms-api.md`.

## 1. Endpoint

| metodo e path | autenticazione | risposta |
|---|---|---|
| `GET /health/live` | no | `{"status": "ok"}`, senza accesso al DB |
| `GET /health/ready` | no | 200 `{"status": "ok"}` se il DB risponde e il contratto delle colonne è rispettato, altrimenti 503 `{"status": "unavailable"}` |
| `GET /v1/organizations` | Bearer | organizzazioni associate alla chiave (`id`, `name`, `website`, `logo_url`, `courses_count`) |
| `GET /v1/organizations/{org_id}/courses` | Bearer | pagina di corsi: `items`, `total`, `limit`, `offset` |
| `GET /v1/organizations/{org_id}/courses/{course_id}` | Bearer | corso completo con moduli, lezioni, URL dei file e test |

Parametri di `/courses`: `limit` (1..100, default 50), `offset` (≥ 0), `status` (confronto
esatto) e `updated_since` (ISO 8601 **con fuso orario**, altrimenti 422). Meglio usare `Z`,
es. `2026-11-01T00:00:00Z`: un `+` del fuso nella query string va codificato come `%2B`,
altrimenti diventa uno spazio e si ottiene 422.

`updated_since` confronta con il più recente di tre istanti, tutti presenti sia nell'elenco
sia nel dettaglio:
- l'aggiornamento del corso (`updated_at`);
- l'ultimo file pronto delle lezioni (`last_generated_at`);
- l'ultima rigenerazione, modifica o approvazione di un test di verifica pronto o approvato
  (`last_assessment_at`).

Limite noto: un test può smettere di essere esposto, perché la rigenerazione è in corso,
fallita o annullata, oppure perché la lezione di verifica è stata eliminata. In questi casi
nessuna data si sposta in avanti, quindi `updated_since` non segnala il corso. Il corso torna
nel filtro quando una nuova generazione finisce. All'LMS conviene un **riallineamento
completo periodico**, cioè una lettura senza `updated_since`, per esempio una volta al
giorno.

L'ordinamento è `updated_at` decrescente.

Nel dettaglio del corso, ogni lezione ha `files` (`dispensa`, `slide`, `discorso`, `video`,
`video_avatar`: URL pubblico su OVH, oppure `null` se il file non è pronto) e `test`, solo per
le lezioni di verifica pronte o approvate. Il test **include le soluzioni**
(`correct_option_id`, `expected_answer`). Gli URL dei file hanno `?v=<epoch>`
quando `generated_at` è valorizzato, altrimenti il parametro manca. Serve a invalidare le
cache.

`logo_url` si costruisce da `logo_path` come nel frontend di a4u: un path relativo diventa
`<OVH_PUBLIC_BASE_URL>/uploads/...`, mentre un valore già assoluto (`http://`, `https://`,
`//...`) o `data:` viene restituito invariato.

Errori: 401 `{"detail": "Credenziali non valide o mancanti."}`, uguale per ogni causa;
404 `{"detail": "Risorsa non trovata."}`, anche per un'organizzazione che esiste ma non è
associata alla chiave (mai 403); 405 `{"detail": "Metodo non consentito."}` per un metodo
diverso da GET su un path esistente, con l'header `Allow`; 422 per parametri non validi; 429
`{"detail": "Troppe richieste."}` con `Retry-After`; 500 `{"detail": "Errore interno."}`.
`/docs`, `/redoc` e `/openapi.json` esistono solo con `DOCS_ENABLED=true`.

## 2. Sviluppo

Requisiti: [uv](https://docs.astral.sh/uv/) e Docker (per il Postgres di test).

```sh
uv sync                                               # crea .venv con le dipendenze di sviluppo
docker compose -f docker-compose.test.yml up -d       # Postgres 16 su 127.0.0.1:55432 (dati in tmpfs)

uv run pytest                                         # tutti i test (meno di 1 minuto)
uv run pytest tests/api/test_api_courses.py           # un solo modulo
uv run ruff check . && uv run ruff format --check .   # lint e formato
uv run mypy app                                       # tipi (strict)

docker compose -f docker-compose.test.yml down        # ferma il Postgres di test
```

I test di `tests/unit/` non richiedono il Postgres. Gli altri usano
`TEST_PG_ADMIN_URL` (default `postgresql://postgres:postgres@127.0.0.1:55432/postgres`):
ricreano il DB `a4u_lms_test` da `tests/schema.sql` e il ruolo `a4u_lms_ro` con gli stessi
GRANT del runbook di a4u.

Avvio locale, solo se serve:
`DATABASE_URL=... OVH_PUBLIC_BASE_URL=... uv run uvicorn --factory app.main:create_app --port 8000`.

La CI (`.github/workflows/ci.yml`) esegue ruff, mypy e pytest con un Postgres di servizio.

## 3. Prerequisiti di produzione

1. **Ruolo `a4u_lms_ro`.** Al 05/11/2026 **non esiste ancora** in produzione. Va creato con il
   runbook §3 di `a4u/docs/lms-api.md`: password (§3.1), creazione e GRANT (§3.2), verifica
   (§3.3). Senza il ruolo il container non parte.
2. **Chiave API.** In a4u: *Admin → Chiavi API LMS*, nuova chiave associata alle
   organizzazioni da esporre. Il token compare **una sola volta**: va consegnato all'LMS
   subito. In a4u resta solo l'hash.
3. **Contratto di lettura in a4u.** Aggiungere `course_lesson.content_generated_at` e
   `course_lesson.content_modified_at` al blocco `lms-read-contract` di `a4u/docs/lms-api.md`
   §1: il servizio le legge per `last_assessment_at`. Non serve un nuovo GRANT, perché su
   `course_lesson` il GRANT è per tabella.

## 4. Build e avvio sulla VM web2 (192.168.40.13)

Il servizio gira in un compose separato da quello di a4u, collegato alla sua rete Docker
`a4u_default`, dove il Postgres risponde come `postgres:5432`.

```sh
git clone <url-del-repository> a4u-lms-api && cd a4u-lms-api
cp .env.example .env && chmod 600 .env
# in .env: password di a4u_lms_ro e nome del DB (POSTGRES_DB del .env di a4u) in DATABASE_URL
docker compose build
docker compose up -d
docker compose ps                                # STATUS "healthy" dopo qualche secondo
# Solo /health e solo dalla VM stessa: la 9002 è HTTP in chiaro.
curl -s http://192.168.40.13:9002/health/ready   # {"status":"ok"}
```

Le chiamate `/v1` passano **sempre** da `https://lms-api.ersaf.it`. Chiamando
`192.168.40.13:9002` direttamente, il Bearer token viaggerebbe in chiaro sulla LAN.

Aggiornamento: `git pull && docker compose build && docker compose up -d`.

Caratteristiche del container:
- uvicorn con **un solo worker** sulla porta 8000, pubblicata su `192.168.40.13:9002`;
- utente non root (uid 10001), filesystem in sola lettura (`/tmp` in tmpfs), nessuna
  capability, `no-new-privileges`, 256 MB di memoria;
- `HEALTHCHECK` su `/health/live`;
- log json-file a rotazione (20 MB × 5).

### Firewall sulla porta 9002

La 9002 accetta connessioni **solo dal proxy 192.168.40.10**. La regola è applicata in
produzione dal 05/11/2026.

Le regole `INPUT`, e quindi `ufw`, **non** proteggono le porte pubblicate da Docker: il
traffico verso i container passa dalla catena `FORWARD` dopo il DNAT. La regola va quindi
nella catena `DOCKER-USER`, che Docker non svuota. Lì la porta è già tradotta in 8000, per
questo si confronta la destinazione originale tramite conntrack:

```sh
sudo iptables -I DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL \
  --ctorigdst 192.168.40.13 --ctorigdstport 9002 \
  ! -s 192.168.40.10 -j DROP
sudo iptables -L DOCKER-USER -n -v --line-numbers   # verifica
```

`--ctdir ORIGINAL` è indispensabile. Le risposte del container appartengono alla stessa
connessione, quindi hanno la stessa destinazione originale `192.168.40.13:9002`, ma come
sorgente hanno l'IP del container. Senza quell'opzione la regola scarterebbe anche il SYN-ACK
verso il proxy, che andrebbe in timeout.

**Persistenza:** la regola non sopravvive a un riavvio. In produzione la reinserisce un'unità
systemd che parte dopo Docker; sul server non c'è `netfilter-persistent` e `ufw` è inattivo.
File `/etc/systemd/system/a4u-lms-api-firewall.service`:

```ini
[Unit]
Description=a4u-lms-api: porta 9002 accettata solo dal proxy 192.168.40.10
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
# $$R: systemd trasforma "$$" in "$", così la variabile arriva intatta a sh.
ExecStart=/bin/sh -c 'R="-p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.40.13 --ctorigdstport 9002 ! -s 192.168.40.10 -j DROP"; iptables -C DOCKER-USER $$R 2>/dev/null || iptables -I DOCKER-USER $$R'

[Install]
WantedBy=multi-user.target
```

```sh
sudo systemctl daemon-reload && sudo systemctl enable --now a4u-lms-api-firewall.service
sudo iptables -S DOCKER-USER   # deve comparire la regola con --ctdir ORIGINAL, una sola volta
```

L'unità è idempotente: `iptables -C` controlla se la regola esiste già, quindi riavviarla non
crea duplicati.

- **Curl dalla VM:** le connessioni partite dalla VM stessa non passano da `FORWARD`, quindi
  il `curl` di `/health` dalla VM continua a funzionare.
- **Verifica:** da un altro host della LAN, `curl -m 3 http://192.168.40.13:9002/health/live`
  deve andare in timeout; dal proxy deve rispondere `{"status":"ok"}`.

## 5. Esposizione TLS

Il TLS lo termina il proxy su 192.168.40.10 (openresty, Nginx Proxy Manager). In produzione
dal 05/11/2026 sono attivi:
- il record DNS `lms-api.ersaf.it → 37.27.237.89`;
- il proxy host verso `http://192.168.40.13:9002`, con certificato Let's Encrypt.

**Da riapplicare:** il blocco *Advanced* con `limit_req` e con la `location /health/` riservata
alle reti interne (vedi sotto). Va inserito dopo aver definito la zona in `http_top.conf` e
riavviato NPM. Finché non c'è, il limite per IP non è attivo e `/health/*` è raggiungibile
da Internet.

Il container accetta gli header `X-Forwarded-*` solo da 192.168.40.10
(`--forwarded-allow-ips`).

**Limite per IP (consigliato).** Il rate limit del servizio è per chiave. Una richiesta con un
token di formato valido ma chiave falsa costa comunque una query al DB, senza limite. Il
proxy deve quindi limitare le richieste per IP: 10/s con burst 20 bastano per un LMS. Oltre
il limite il proxy risponde 429.

**Nginx Proxy Manager** → *Hosts → Proxy Hosts → Add Proxy Host*:

| campo | valore |
|---|---|
| Domain Names | `lms-api.ersaf.it` |
| Scheme | `http` |
| Forward Hostname / IP | `192.168.40.13` |
| Forward Port | `9002` |
| Block Common Exploits | attivo |
| Websockets Support | disattivo |
| SSL → SSL Certificate | *Request a new SSL Certificate* (Let's Encrypt) |
| SSL → Force SSL, HTTP/2 Support | attivi (HSTS a scelta) |
| Advanced | `limit_req zone=lms_api_per_ip burst=20 nodelay; limit_req_status 429;` più, facoltativi, `client_max_body_size 16k; proxy_read_timeout 60s;` |

In NPM la `limit_req_zone` va nel contesto `http`, non nel tab *Advanced*, che finisce nel
blocco `server`. **L'ordine conta:**

1. Definire la zona nel file `/data/nginx/custom/http_top.conf` del container NPM:
   `limit_req_zone $binary_remote_addr zone=lms_api_per_ip:10m rate=10r/s;`
2. Riavviare NPM.
3. Solo dopo, inserire le righe `limit_req` nel tab *Advanced*.

Se le righe `limit_req` arrivano prima della zona, `nginx -t` fallisce e il proxy host
diventa «Offline».

Per tenere `/health/*` interno, nel tab *Advanced* si aggiunge il blocco
`location /health/` completo, con `allow`/`deny`, `proxy_pass` e gli stessi `proxy_set_header`
del blocco nginx qui sotto:

```nginx
location /health/ {
    allow 192.168.40.0/24;
    allow 127.0.0.1;
    deny  all;

    proxy_pass http://192.168.40.13:9002;
    proxy_set_header Host              $host;
    proxy_set_header X-Forwarded-For   $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Blocco nginx equivalente, se il proxy si configura a mano:

```nginx
# contesto http: un contatore per IP del client
limit_req_zone $binary_remote_addr zone=lms_api_per_ip:10m rate=10r/s;

server {
    listen 80;
    server_name lms-api.ersaf.it;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name lms-api.ersaf.it;

    ssl_certificate     /etc/letsencrypt/live/lms-api.ersaf.it/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/lms-api.ersaf.it/privkey.pem;

    client_max_body_size 16k;          # l'API accetta solo GET

    location / {
        # Facoltativo: solo letture (GET implica HEAD).
        limit_except GET { deny all; }

        limit_req zone=lms_api_per_ip burst=20 nodelay;
        limit_req_status 429;

        proxy_pass http://192.168.40.13:9002;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 60s;
    }

    # /health/* solo dalle reti interne (da adattare: LAN, VPN, monitoraggio).
    # /health/ready prende una connessione al DB a ogni chiamata: da Internet non serve.
    location /health/ {
        allow 192.168.40.0/24;
        allow 127.0.0.1;
        deny  all;

        proxy_pass http://192.168.40.13:9002;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 6. Esempi

```sh
BASE=https://lms-api.ersaf.it
TOKEN='a4u_lms_xxxxxxxx_...'   # meglio da un file o da una variabile, non nella history

# /health/* risponde solo dalle reti interne, se il proxy è configurato come al §5
curl -s "$BASE/health/live"
curl -s "$BASE/health/ready"

curl -s -H "Authorization: Bearer $TOKEN" "$BASE/v1/organizations"

curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/organizations/<org_id>/courses?limit=20&offset=0&status=published"

# Solo i corsi cambiati da una certa data. Il fuso è obbligatorio: usare Z (UTC),
# oppure codificare il "+" come %2B (un "+" nudo diventa uno spazio → 422).
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/organizations/<org_id>/courses?updated_since=2026-11-01T00:00:00Z"
curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/organizations/<org_id>/courses?updated_since=2026-11-01T00:00:00%2B01:00"

curl -s -H "Authorization: Bearer $TOKEN" \
  "$BASE/v1/organizations/<org_id>/courses/<course_id>"
```

I valori di `status` sono quelli di `COURSE_STATUSES` in a4u (`backend/app/models/course.py`):
`draft`, gli stati di avanzamento `*_pending`, `*_ready` e `*_approved` (dall'architettura fino
all'avatar), poi `published` e `archived`. Uno status sconosciuto restituisce una lista vuota,
non un errore.

## Per l'integratore LMS

La guida completa per i programmatori dell'LMS è
[`docs/integrazione-lms.md`](docs/integrazione-lms.md), con gli esempi in [`docs/esempi/`](docs/esempi/): risposte reali
anonimizzate, tranne il test di verifica e i percorsi dei file, che sono fittizi. In sintesi:

- **Campi da sanificare.** `website` e `logo_url` arrivano **così come sono salvati in a4u**,
  senza filtri sullo schema:
  - `logo_url` può essere un URL `http://` o `https://` di un altro host, oppure un `data:`
    di qualsiasi tipo;
  - `website` è testo libero e può contenere anche `javascript:`.

  Prima di inserirli in una pagina HTML vanno validati: accettare solo `https://` (o
  `http://`) e `data:image/(png|jpeg|gif|webp);base64,` per il logo, mettere il valore in
  attributi con escape e non in `innerHTML`. Altrimenti il rischio è un XSS memorizzato
  nell'LMS.
- **Testo dei corsi.** Lo stesso vale per i testi (titoli, descrizioni, domande): sono
  testo semplice, da mostrare con escape.
- **Soluzioni dei test.** Il campo `test` contiene le soluzioni (`correct_option_id`,
  `expected_answer`): non vanno mandate al browser dello studente.
- **Cache dei file.** Gli URL dei file cambiano (`?v=`) a ogni rigenerazione. Per la
  sincronizzazione incrementale si usa `updated_since`.

## 7. Autenticazione, cache e rate limit

- **Token:** `a4u_lms_<key_prefix>_<secret>`, con `key_prefix` di 8 caratteri `[a-z0-9]` e
  `secret` di 43 caratteri `[A-Za-z0-9_-]`. Il secret può contenere `_`: il token si legge per
  posizione, mai con `split("_")`. Il servizio confronta lo SHA-256 del token con
  `lms_api_keys.key_hash`. Una chiave è valida se non è revocata né scaduta.
- **Cache dell'autenticazione:** gli esiti positivi restano in memoria per
  `AUTH_CACHE_SECONDS` (default 30 s), mai oltre la scadenza della chiave. Quindi una revoca, o
  un cambio delle organizzazioni associate, ha effetto **entro 30 s**. Con `0` la cache è
  disattivata.
- **Rate limit:** token bucket per chiave, `RATE_LIMIT_PER_MIN` richieste al minuto (default
  120). Oltre il limite si riceve 429 con `Retry-After`. `/health/*` non ha limite.
- **Un solo worker uvicorn.** Cache e rate limit sono in memoria per processo: con più worker
  o più repliche ogni processo avrebbe la sua cache e il suo limite, e il limite reale si
  moltiplicherebbe. Non aumentare `--workers`.

## 8. Rotazione di una chiave

1. In a4u, *Admin → Chiavi API LMS*: creare una nuova chiave con le stesse organizzazioni.
2. Aggiornare il token nella configurazione dell'LMS e verificare una chiamata a
   `/v1/organizations`.
3. Revocare la vecchia chiave in a4u. Smette di funzionare entro `AUTH_CACHE_SECONDS`.

Un token perso non si recupera: si revoca la chiave e se ne crea una nuova.

## 9. Il controllo del contratto fallisce dopo una migrazione di a4u

All'avvio, e ogni 30 s su `/health/ready`, il servizio controlla che il ruolo veda tutte le
colonne dichiarate in `app/db/tables.py`, usando `information_schema.columns`. Quella vista
mostra solo le colonne su cui il ruolo ha un permesso, quindi il controllo verifica insieme
schema e GRANT.

**Sintomi:**
- il container è in restart;
- nei log c'è `"event": "contract_check_failed"` con
  `"error": "colonne mancanti: tabella.colonna, ..."`;
- `/health/ready` risponde 503 e nei log compare `health_not_ready`.

**Cosa fare:**
- Se a4u ha **rinominato o tolto** la colonna:
  1. allineare `app/db/tables.py`, `app/db/queries.py` e `tests/schema.sql` alla nuova
     migrazione;
  2. aggiornare `docs/contracts/db-read.md`;
  3. far girare i test, poi rifare build e avvio.
- In alternativa, se la modifica in a4u è un errore, ripristinare la colonna in a4u.
- Se la colonna esiste ma **manca il GRANT**, eseguire il GRANT dal runbook (§3.2 e §3.4 di
  `a4u/docs/lms-api.md`).
  - Per le tabelle intere: `GRANT SELECT ON TABLE <tabella> TO a4u_lms_ro;`.
  - Per `users` il permesso è **per colonna** e va esteso a mano:
    `GRANT SELECT (id, full_name, email) ON users TO a4u_lms_ro;`.

Vale anche per `content_generated_at` e `content_modified_at` di `course_lesson`: devono stare
nel contratto di lettura di a4u, come indicato nel §3, passo 3.

## 10. Verifica degli URL dei file (`verify-urls`)

a4u in produzione ha `STORAGE_LOCAL_FALLBACK=true`: alcuni file vecchi potrebbero essere solo
sul disco del server e non su OVH. In quel caso l'API restituisce comunque l'URL OVH, che però
dà 404. Per controllare un'organizzazione, o un solo corso, prima di consegnarla all'LMS:

```sh
docker compose exec lms-api python -m app.cli verify-urls --org <org_uuid>
docker compose exec lms-api python -m app.cli verify-urls --org <org_uuid> --course <course_uuid>
# opzioni: --concurrency 4 (HEAD in parallelo), --timeout 15 (secondi)
```

Il comando fa una `HEAD`, seguendo i redirect, su ogni URL che inizia con
`OVH_PUBLIC_BASE_URL/`, logo compreso. Gli URL di altri host, per esempio un logo con un URL
assoluto esterno, **non** vengono contattati: compaiono come `saltato (host esterno)` e non
cambiano il codice di uscita. I loghi `data:` non si controllano.

Corsi e lezioni si leggono in una sola transazione `REPEATABLE READ`. Lo snapshot è
coerente: un corso aggiornato durante la scansione non si salta e non si conta due volte.

Il comando stampa:
- su stdout, gli URL che non rispondono 200 (`status  etichetta  url`), quelli saltati e un
  riepilogo;
- su stderr, in JSON, i warning (per esempio `unsafe_file_path`).

Codici di uscita:
- `0`: tutti gli URL rispondono 200;
- `1`: almeno un URL non risponde 200;
- `2`: configurazione non valida, DB non raggiungibile, oppure organizzazione o corso
  inesistenti.

## 11. Log

Una riga JSON per record su stdout (`docker compose logs lms-api`), con i campi `ts`
(ISO 8601 UTC), `level`, `logger`, `event` e gli extra. Le eccezioni hanno il traceback nel
campo `exc`.

- **Accesso**, una riga per richiesta: `event="access"`, `key_prefix` (o `null`), `method`,
  `path` (senza query string), `status`, `duration_ms`. Il log di accesso di uvicorn è
  disattivato.
- **Eventi utili:**
  - `contract_check_failed`, `health_not_ready`: DB o contratto (vedi §9);
  - `unsafe_file_path`: path di file rifiutato, con `lesson_id` e `kind`;
  - `assessment_question_skipped`: domanda del test non valida e saltata;
  - `unhandled_exception`: errore 500, con traceback.
- **Mai nei log:** il token, l'header `Authorization`, la query string, `DATABASE_URL` e il
  testo delle domande dei test.

## Configurazione

Variabili in `.env` (modello in `.env.example`, dettaglio in `docs/contracts/config.md`):

| variabile | default | note |
|---|---|---|
| `DATABASE_URL` | obbligatoria | `postgresql://a4u_lms_ro:<password>@postgres:5432/<POSTGRES_DB>` |
| `OVH_PUBLIC_BASE_URL` | obbligatoria | `https://progettiersaf.com/media` |
| `RATE_LIMIT_PER_MIN` | `120` | richieste al minuto per chiave |
| `AUTH_CACHE_SECONDS` | `30` | `0` = cache disattivata |
| `DB_STATEMENT_TIMEOUT_MS` | `10000` | timeout delle query |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | `3` / `4` | pool di connessioni |
| `DOCS_ENABLED` | `false` | `/docs`, `/redoc`, `/openapi.json` |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

Limiti fissi nel codice (`app/db/engine.py`, `docs/contracts/db-read.md` §3):
- **Connessione:** al massimo **5 s** per aprire una connessione a Postgres (`timeout` di
  asyncpg);
- **Pool:** al massimo **10 s** di attesa per una connessione libera (`pool_timeout`, pool
  di 3 + 4);
- **Query:** `DB_STATEMENT_TIMEOUT_MS` per ogni query.

Le sessioni sono `default_transaction_read_only=on`, con `application_name=a4u-lms-api`
visibile in `pg_stat_activity`. Se il DB non risponde, `/health/ready` dà 503 e le richieste
`/v1` falliscono in pochi secondi, invece di restare appese.
