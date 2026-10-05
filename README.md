# PokéBot 30th — bot per completare il master set *Pokémon 30th Celebration*

Cerca in automatico su **Wallapop, Vinted ed eBay.it** le carte che ti mancano del set
**30th Celebration** (161 carte, numerazione `/128`) e della sottoserie **Classic Collection** (30 carte),
e ti manda su **Telegram** il link di ogni annuncio utile.

Regole del bot:

- **Carte singole**: notifica ogni annuncio di una carta che ti manca, pronta e disponibile.
  Non sceglie "la più bella": ti manda tutto ciò che è acquistabile subito.
- **Lotti**: notifica solo se **almeno il 50 %** delle carte del lotto (percentuale modificabile) è tra quelle che ti mancano.
  Il "set completo / master set" è trattato come un lotto con tutte le 161 carte.
- **Scarta**: aste (su eBay chiede solo *Compralo Subito*), annunci "cerco/compro", solo scambio, venduti/riservati,
  preordini, proxy/fake, codici online, prodotti sigillati (buste, ETB, box…), accessori, e il set *Celebrations* del 25°.
- **Non rinotifica** mai lo stesso annuncio.

## Senza PC: gratis su GitHub Actions, comandi da Telegram (consigliato)

Il bot gira ogni 20 minuti sui server di GitHub e si comanda **solo da Telegram**, anche da iPhone.

1. Su Telegram scrivi a **@BotFather** → `/newbot` (o `/token` se il bot esiste già) e copia il token.
2. Apri <https://github.com/simonecassu/BotScraping/settings/secrets/actions/new>:
   *Name* `TELEGRAM_BOT_TOKEN`, *Secret* il token → **Add secret**.
3. Apri la scheda **Actions** del repository → workflow **PokéBot 30th** → **Run workflow** (oppure aspetta il prossimo giro).
4. Su Telegram scrivi **/start** al tuo bot: al primo giro il bot si collega da solo alla tua chat (nessun chat id da cercare).
5. Segna le carte mancanti con i comandi, ad esempio `/aggiungi 131 132 149-152`, `/aggiungi sir`, `/aggiungi tutte`.

| Comando | Effetto |
|---|---|
| `/mancanti` | elenco delle carte che ti mancano |
| `/aggiungi 131 132 149-152 c4` | segna come mancanti (numeri, intervalli, `c1..c30` per la Classic) |
| `/aggiungi ir` · `sir` · `pr` · `pikachu ex` · `tutte` · `classic` | per rarità, per nome, tutto il set |
| `/rimuovi 131` (o `/ho 131`) | trovata: toglila |
| `/lista` · `/lista classic` | tutte le carte con i numeri |
| `/stato` | ultimo ciclo, errori, impostazioni |
| `/storico` | cartelle per carta con tutti gli annunci trovati, divisi per eBay / Vinted / Wallapop (pulsanti) |
| `/soglia 50` · `/prezzo 100` · `/fonti wallapop vinted ebay` | soglia lotti, prezzo massimo, marketplace |
| `/intervallo 20` | ogni quanti minuti fare la ricerca automatica |
| `/lingua ita` · `/lingua tutte` | scarta gli annunci in francese/inglese/altre lingue (default) oppure accetta tutto |
| `/cerca` · `/resetvisti` | ricerca immediata · rinotifica gli annunci già visti |

Lo stato (carte mancanti, annunci già visti) è salvato nel branch `bot-state`.
Solo la chat che ha scritto `/start` per prima può comandare il bot. Aggiungendo il secret `TELEGRAM_CHAT_ID`
(il tuo chat id, lo trovi nel log del primo giro o chiedendolo a @userinfobot) anche il ponte ignora gli estranei.
GitHub disattiva i workflow pianificati dopo 60 giorni senza attività sul repository: arriva un'email e si riattiva con un tap.

### Risposta immediata: il ponte Telegram → GitHub

Senza ponte il bot legge i comandi solo quando gira (ogni 20 minuti). Con il ponte, gratuito su Cloudflare Workers,
**ogni messaggio al bot avvia subito il workflow**: il ponte risponde "Ricevuto" e il bot risponde entro un minuto.
Lo pubblica GitHub Actions al posto tuo: devi solo inserire tre codici nei secret del repository.

1. **Token GitHub** (permette al ponte di avviare il bot): <https://github.com/settings/personal-access-tokens/new> →
   nome a piacere, scadenza la più lunga, *Repository access* → **Only select repositories** → `BotScraping`,
   *Repository permissions* → **Contents: Read and write** → **Generate token** → copia (inizia con `github_pat_`).
2. **Token Cloudflare** (account gratuito su <https://dash.cloudflare.com>): <https://dash.cloudflare.com/profile/api-tokens> →
   **Create Token** → modello **Edit Cloudflare Workers** → **Use template** → **Continue to summary** → **Create Token** → copia.
3. **Account ID Cloudflare**: nella pagina **Workers & Pages**, colonna di destra, voce *Account ID* (è anche il codice
   lungo nell'indirizzo `dash.cloudflare.com/<account-id>/...`).
4. **Secret su GitHub**: <https://github.com/simonecassu/BotScraping/settings/secrets/actions> → **New repository secret**, tre volte:
   `BRIDGE_GITHUB_TOKEN` (punto 1), `CLOUDFLARE_API_TOKEN` (punto 2), `CLOUDFLARE_ACCOUNT_ID` (punto 3).
5. **Pubblica**: <https://github.com/simonecassu/BotScraping/actions/workflows/ponte.yml> → **Run workflow**.
   Alla fine il log mostra "Ponte attivo" e l'indirizzo del worker.

Il ponte ha anche un **timer** (ogni 20 minuti) che avvia la ricerca periodica: il cron di GitHub resta solo come riserva.
L'indirizzo del worker mostra lo stato del ponte; aggiungendo `/reset` lo disattivi (si torna alla lettura dei comandi
a ogni giro). Con il ponte attivo, se mandi più comandi in pochi secondi mettili in **un solo messaggio, uno per riga**.
Il file del worker è `deploy/cloudflare-worker.js`, la sua configurazione `deploy/wrangler.toml`.

## Avvio rapido su PC

```bash
git clone <questo repo> && cd BotScraping
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # inserisci TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
python run.py               # interfaccia su http://localhost:8080
```

Con Docker: `cp .env.example .env`, compila il file, poi `docker compose up -d`.

### Telegram
Basta il token di @BotFather in `TELEGRAM_BOT_TOKEN`: al primo `/start` il bot salva da solo la chat.
`TELEGRAM_CHAT_ID` è facoltativo (se impostato, solo quella chat è accettata).
Anche su PC i comandi Telegram della tabella qui sopra funzionano, in tempo reale.

## Come si usa

1. **Checklist** (`/`): spunta le carte che **ti mancano** (filtro per nome/numero/rarità, selezione per rarità,
   "tutte/nessuna/inverti"). Il salvataggio è automatico. Il bot cerca solo quelle.
2. **Impostazioni** (`/impostazioni`): ogni quanti minuti cercare, prezzo massimo, percentuale minima per i lotti,
   quali marketplace, query generiche, ricerca per singola carta (es. `Lapras 131/128`, a rotazione per non martellare i siti),
   parole che identificano il set negli annunci.
3. **Trovati** (`/trovati`): registro degli annunci notificati con le carte riconosciute, esito dei cicli ed eventuali
   errori per marketplace (es. blocco anti-bot). Pulsante **Cerca ora** per non aspettare il prossimo ciclo.

Da riga di comando:

```bash
python cli.py cerca --dry-run                 # un ciclo senza inviare notifiche
python cli.py cerca                           # un ciclo con notifiche
python cli.py test-telegram
python cli.py mancanti
python cli.py actions                         # un passaggio completo (comandi Telegram + ricerca), per cron
python cli.py analizza "Lotto 30th: Lapras 131/128, Moltres 130/128"   # come viene classificato un annuncio
```

## Come riconosce le carte

Il testo (titolo + descrizione) viene normalizzato e si cercano:

- numeri espliciti `n/128` (identificano il set anche senza altre parole);
- nomi delle carte, dal più lungo al più corto (così "Mew ex" non fa scattare anche "Mew"), con eventuale numero
  subito dopo il nome (`Pikachu ex 149`). Un nome con più versioni e senza numero (es. 30 "Pikachu" diversi)
  viene notificato come **da verificare** se almeno una versione ti manca;
- per la Classic Collection (numerazione originale, es. Charizard 4/102) serve la parola "classic" nell'annuncio.

Un annuncio è un **lotto** se contiene parole come lotto/bundle/collezione/`x5`/`5 carte` oppure più carte diverse.
Se dichiara "20 carte" ma se ne riconoscono 2, il rapporto è calcolato su 20. Un lotto del set in cui non si
capisce quali carte ci sono **non** viene notificato (attivabile in Impostazioni).

Nomi alternativi (es. nome italiano di un Allenatore) si aggiungono in `data/aliases.json`.

## Marketplace e limiti

| Fonte | Metodo | Note |
|---|---|---|
| Wallapop | API JSON del sito (`api.wallapop.com`) | ordinati per data, filtrati sull'Italia; esclusi riservati/venduti |
| Vinted | API catalogo con sessione anonima | può richiedere qualche secondo tra le richieste |
| eBay.it | **API Browse ufficiale** se imposti `EBAY_CLIENT_ID/SECRET`, altrimenti pagina di ricerca | solo *Compralo Subito*, solo venditori in Italia (opzione) |

Gli scraper non ufficiali possono smettere di funzionare se il sito cambia o blocca il bot (HTTP 403/429):
l'errore compare nella pagina **Trovati**, il ciclo continua con gli altri marketplace. Per eBay conviene creare
le chiavi gratuite su <https://developer.ebay.com/my/keys> e usare l'API, molto più stabile.
Cardmarket non è incluso perché protetto da Cloudflare.

## Dati del set

`data/sets/me55.json` e `data/sets/me55c.json` provengono dal dataset open
[pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data) (immagini incluse).
Per aggiornarli o aggiungere un altro set: `python scripts/import_set.py me55 --printed-total 128`.

## Sviluppo

```bash
pip install -r requirements-dev.txt
pytest
```

Struttura: `pokebot/cards.py` (set e indice), `pokebot/matcher.py` (riconoscimento e regole singola/lotto),
`pokebot/scrapers/` (un modulo per marketplace), `pokebot/search.py` (ciclo di ricerca), `pokebot/notifier.py`
(Telegram), `pokebot/telegram_bot.py` (comandi Telegram), `pokebot/scheduler.py` (esecuzione periodica),
`pokebot/web/` (interfaccia Flask), `.github/workflows/bot.yml` (esecuzione su GitHub Actions),
`deploy/cloudflare-worker.js` + `deploy/wrangler.toml` (ponte Telegram → GitHub), `.github/workflows/ponte.yml` (ne fa il deploy).
