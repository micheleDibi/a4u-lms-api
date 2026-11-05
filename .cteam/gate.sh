#!/usr/bin/env bash
# CTEAM_TEMPLATE  (il lead rimuove questa riga quando riscrive il gate da CLAUDE.md)
# Gate di qualità del progetto: eseguito a ogni task marcato come completato.
# Exit != 0  => il completamento viene bloccato e l'output torna all'operatore.
# Variabili disponibili: $CTEAM_TASK (titolo del task), $CTEAM_TEAMMATE (chi lo chiude),
# $CTEAM_WORKTREE (cartella dell'operatore in isolamento worktree, vuota altrimenti).
# Il gate parte dalla radice del progetto, o dal worktree dell'operatore: usa percorsi relativi.
# PERSONALIZZALO: tienilo VELOCE (secondi, non minuti), perché gira a ogni completamento.
set -uo pipefail
fail=0
run() { echo "▶ $*"; "$@" || fail=1; }

# Esempio per ruolo:
# case "$CTEAM_TEAMMATE" in
#   frontend*) run npm --prefix frontend run -s lint; run npm --prefix frontend run -s typecheck ;;
#   backend*)  run python -m pytest -q -x backend/tests ;;
#   db)        run python -m alembic check ;;
# esac

# Rilevamento automatico prudente (solo lint/typecheck, se esistono gli script)
if [[ -f package.json ]] && command -v npm >/dev/null; then
  for s in lint typecheck; do
    jq -e --arg s "$s" '.scripts[$s]' package.json >/dev/null 2>&1 && run npm run -s "$s"
  done
fi
if [[ -f pyproject.toml ]] && command -v ruff >/dev/null; then run ruff check .; fi

exit $fail
