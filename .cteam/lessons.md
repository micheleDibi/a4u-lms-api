# Lezioni delle corse precedenti (a4u-lms-api)

- Gate in shared: dalla prima corsa restringere ruff/format/mypy ai file del ruolo e usare `mypy --follow-imports=silent --ignore-missing-imports`. Il controllo globale lo fa solo il lead: il lavoro in corso di un operatore bloccava i completamenti degli altri (backend-b 9 gate falliti).
- Le fixture DB condivise devono usare un DB per ruolo (`TEST_DB_NAME`): gate concorrenti sullo stesso Postgres si distruggono il DB a vicenda.
- Nei task di scaffold elencare già `pythonpath = ["."]` (pytest importlib + `package = false`) e le per-file-ignores dei test (S101/S105/S106/S608), e testare il gate con bash 3.2 (array vuoti con `set -u`).
- Un operatore chiude il task prima di leggere una richiesta di modifica: quando serve una correzione su un file di un task chiuso, creare subito un nuovo task con `Files:` invece di mandare messaggi.
- Evitare dipendenze circolari tra perimetri (Settings ↔ engine, main ↔ auth): spezzare il nucleo in un task piccolo e prioritario che sblocca gli altri.
- Prima di creare teammate controllare con ListAgents chi è ancora vivo: gli operatori della corsa precedente prendono subito i task assegnati al loro nome, e i nuovi nascono come `<nome>-2` (doppioni sugli stessi file). Riusare quelli vivi via SendMessage invece di crearne altri.
- Con task completati, il controllo FUORI PERIMETRO segnala i loro file (task chiusi non più in elenco): verificare i perimetri originali prima di ripristinare, poi committare subito.
- Prima di mettere dati reali di produzione nel repo, verificare la visibilità con `gh repo view --json visibility` (può cambiare durante la sessione): con repo pubblico, test, soluzioni e URL dei file vanno resi fittizi.
- Quando un task si sblocca, l'operatore assegnato (es. reviewer) non sempre se ne accorge: mandargli subito un messaggio.
