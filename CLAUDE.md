# Progetto: [NOME]

[1-2 righe: cosa fa, per chi, dove gira (locale / staging / produzione)]

## Stack
- Linguaggio/versione: [es. Python 3.12 / Java 17 / PHP 8.2]
- Framework: [es. FastAPI / Tomcat / Laravel]
- Database: [es. MySQL 8 — ambiente di sviluppo: ...]

## Mappa del codice
- `[cartella]/` — [cosa contiene]
- `[cartella]/` — [cosa contiene]
- Punto di ingresso: `[file]`
- Configurazione: `[file]` (NON modificare senza chiedere)

## Comandi
- Avvio locale: `[comando]`
- Test di un singolo file/modulo: `[comando]`
- Test completi: `[comando]` (durata circa [X] min)
- Lint/format: `[comando]`

## Convenzioni
- [es. nomi di variabili in inglese, commenti in italiano]
- [es. gestione errori: eccezioni custom in `errors.py`]
- [es. query sempre tramite repository, mai SQL inline nei controller]

## Da non toccare
- [file/cartelle generati, legacy, condivisi con altri sistemi]

## Trappole note
- [es. i test di integrazione richiedono il DB locale attivo]
- [es. encoding dei file .docx: usare lxml, non python-docx]

## cteam (solo se usi il team: `cteam`)
Il lead ricava da qui ruoli, perimetri e gate; i valori vanno poi in `.cteam/config.json`.
- Ruoli e cartelle: `backend` → `[cartelle]` · `frontend` → `[cartelle]` · `db` → `[cartelle]` · `reviewer` → `[cartella dei test e2e]`
- Porte di sviluppo: backend `[porta]`, frontend `[porta]`
- Contratti: `[cartella, es. docs/contracts/]`
- Gate per ruolo (veloce, secondi): backend `[comando]` · frontend `[comando]` · db `[comando]`
- Isolamento: `shared` (un solo albero di lavoro) oppure `worktree` (un worktree git per operatore)
- Da collegare nei worktree: `[cartelle non tracciate, es. node_modules, backend/.venv]`
