#!/usr/bin/env bash
# Salva lo stato del bot (database cifrato + dati per la Mini App) nel branch "bot-state". Lo chiama bot.yml due volte:
# subito dopo i comandi Telegram (così la Mini App vede le modifiche in mezzo minuto) e di nuovo dopo la ricerca.
# Si risalva solo se il database o il codice sono cambiati dall'ultimo salvataggio (/tmp/db-hash-before,
# /tmp/code-sha-before, scritti dal ripristino e aggiornati qui). I comandi eseguiti escono dalla coda (queue-ack)
# solo dopo il salvataggio: se il giro si interrompe, il prossimo li ritrova.
# Variabili: TELEGRAM_BOT_TOKEN (cifratura), GITHUB_TOKEN, GITHUB_SHA, GITHUB_REPOSITORY.
set -euo pipefail
test -f data/pokebot.db || exit 0
if [ "$(python cli.py db-hash)" = "$(cat /tmp/db-hash-before 2>/dev/null)" ] && [ "$(cat /tmp/code-sha-before 2>/dev/null)" = "$GITHUB_SHA" ]; then
  echo "nulla è cambiato: stato non risalvato"; python cli.py queue-ack; exit 0
fi
rm -rf /tmp/state && mkdir -p /tmp/state
python cli.py export-json /tmp/state || echo "::warning::stato per la Mini App non scritto"
# dopo l'export, così eventuali album creati dall'export restano nel database
python cli.py seal data/pokebot.db /tmp/state/pokebot.db.enc
echo "$GITHUB_SHA" > /tmp/state/code-sha
(
  cd /tmp/state
  git init -q -b bot-state
  git -c user.name=pokebot -c user.email=pokebot@users.noreply.github.com add .
  git -c user.name=pokebot -c user.email=pokebot@users.noreply.github.com commit -q -m "stato"
  git push -q --force "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git" bot-state
)
python cli.py db-hash > /tmp/db-hash-before   # l'impronta di quello che è stato salvato
echo "$GITHUB_SHA" > /tmp/code-sha-before
python cli.py queue-ack
echo "stato salvato"
