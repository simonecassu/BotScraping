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

Il bot si sveglia ogni 5 minuti sui server di GitHub (ricerca completa ogni 20 minuti, o quanto dici con `/intervallo`; gli inseguimenti `/insegui` a ogni sveglia) e si comanda **solo da Telegram**, anche da iPhone.

1. Su Telegram scrivi a **@BotFather** → `/newbot` (o `/token` se il bot esiste già) e copia il token.
2. Apri <https://github.com/simonecassu/BotScraping/settings/secrets/actions/new>:
   *Name* `TELEGRAM_BOT_TOKEN`, *Secret* il token → **Add secret**.
3. Apri la scheda **Actions** del repository → workflow **PokéBot 30th** → **Run workflow** (oppure aspetta il prossimo giro).
4. Su Telegram scrivi **/start** al tuo bot: al primo giro il bot si collega da solo alla tua chat (nessun chat id da cercare).
5. Segna le carte mancanti con i comandi, ad esempio `/aggiungi 131 132 149-152`, `/aggiungi sir`, `/aggiungi tutte`.

| Comando | Effetto |
|---|---|
| `/mancanti` | elenco delle carte che ti mancano |
| `/collezione` · `/collezione sv8` · `/collezione 30th` | collezioni seguite · passa a un'altra collezione del catalogo (scaricata al volo, parte da "mi mancano tutte"): da lì tutti i comandi lavorano su di lei · torna alla 30th |
| `/collezione sv8 attiva` · `disattiva` | il bot cerca (o smette di cercare) anche quella collezione; una sua carta si indica ovunque come `sv8:7` |
| `/aggiungi 131 132 149-152 c4` | segna come mancanti nella collezione corrente (numeri, intervalli, `c1..c30` per la Classic) |
| `/aggiungi ir` · `sir` · `pr` · `pikachu ex` · `tutte` · `classic` | per rarità, per nome, tutto il set |
| `/rimuovi 131` (o `/ho 131`) | trovata: toglila |
| `/lista` · `/lista classic` | tutte le carte con i numeri |
| `/stato` | ultimo ciclo, errori, impostazioni |
| `/storico` | cartelle per carta con tutti gli annunci trovati, divisi per eBay / Vinted / Wallapop (pulsanti) |
| `/soglia 50` · `/prezzo 100` · `/fonti wallapop vinted ebay` | soglia lotti, prezzo massimo, marketplace |
| `/intervallo 20` | ogni quanti minuti fare la ricerca automatica |
| `/lingua ita` · `/lingua tutte` | scarta gli annunci in francese/inglese/altre lingue (default) oppure accetta tutto |
| `/prezzi 145` · `/prezzi` | prezzi visti per una carta (min/mediana/max per marketplace, tendenza) · quanto costa finire il set |
| `/progresso` | avanzamento del set, mancanti per rarità, stima di spesa |
| `/affari 60` · `/affari off` | avviso 🔥 immediato se una carta mancante esce sotto il 60 % della sua mediana storica |
| `/pausa` · `/riprendi` · `/notte 23 8` | sospende le notifiche (il bot accumula e invia tutto al ritorno) · ore silenziose |
| `/esporta` | file Excel con checklist, storico annunci e prezzi |
| `/immagini on` · `off` | immagine della carta nei messaggi raggruppati |
| `/cerca` · `/cerca 145` · `/resetvisti` | ricerca immediata · ricerca mirata di una carta (tutto ciò che è in vendita ora, dal più economico) · rinotifica gli annunci già visti |
| `/insegui 151` · `/insegui 151 2h ogni 10m` · `/insegui` · `/insegui stop` | per 6 ore cerca quella carta a ogni sveglia del bot (5 minuti) e avvisa solo sugli annunci nuovi, poi manda il riepilogo; durata (max 48h) e frequenza (min 5 min) a scelta, più carte insieme · elenco degli attivi · ferma tutti (o `/insegui stop 151`) |

Lo stato (carte mancanti, annunci già visti) è salvato nel branch `bot-state`.
Solo la chat che ha scritto `/start` per prima può comandare il bot. Aggiungendo il secret `TELEGRAM_CHAT_ID`
(il tuo chat id, lo trovi nel log del primo giro o chiedendolo a @userinfobot) anche il ponte ignora gli estranei.
GitHub disattiva i workflow pianificati dopo 60 giorni senza attività sul repository: arriva un'email e si riattiva con un tap.

### Mini App dentro Telegram

La home "Pokébot" ha quattro sezioni: **Collezioni** (la 30th Celebration più tutte le altre collezioni del catalogo pubblico, ognuna con la sua checklist: carte possedute accese, mancanti al buio), **Inseguite**, **Trovati** (annunci e prezzi) e **Impostazioni**. Le altre collezioni si possono spuntare liberamente, ma il bot le cerca solo se le attivi dalla loro pagina (interruttore "Ricerca sui marketplace"): per non essere sommersi conviene tenere accesa solo la 30th.

Con il ponte attivo, accanto alla chat compare il pulsante **App**: apre una pagina dentro Telegram con la checklist
grafica (tocchi una carta per segnarla mancante o presa), le cartelle degli annunci trovati con foto e prezzi divisi
per marketplace, i prezzi per carta con progresso e stima di spesa, e le impostazioni con interruttori.
Ogni tocco diventa un comando per il bot; solo il proprietario (chi ha fatto `/start`) può usarla.
I dati arrivano da `state.json`, pubblicato nel branch `bot-state` a ogni giro. Pagina: `deploy/app/index.html`.

### Risposta immediata: il ponte Telegram → GitHub

Senza ponte il bot legge i comandi solo quando gira (ogni 5 minuti). Con il ponte, gratuito su Cloudflare Workers,
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

Il ponte ha anche un **timer** (ogni 5 minuti) che sveglia il bot: inseguimenti `/insegui 151` e, quando è ora, la ricerca completa: il cron di GitHub resta solo come riserva.
L'indirizzo del worker mostra lo stato del ponte; aggiungendo `/reset` lo disattivi (si torna alla lettura dei comandi
a ogni giro). Con il ponte attivo, se mandi più comandi in pochi secondi mettili in **un solo messaggio, uno per riga**.
Il file del worker è `deploy/cloudflare-worker.js`, la sua configurazione `deploy/wrangler.toml`.

### Ognuno il suo Pokébot, album in comune a scelta

Il primo che scrive `/start` al bot ne diventa il proprietario e con `/invita` genera codici per collegare altre persone (`/start CODICE`). Ogni persona collegata ha il **suo** Pokébot: le sue collezioni, le sue impostazioni (pausa, ore silenziose, foto, affari…), i suoi inseguimenti, la sua Mini App. Condivise sono solo la ricerca (gira una volta per tutti) e lo storico dei prezzi.

Per mettere un album in comune: `/amico` dà un codice; chi lo scrive diventa amico. Poi `/condividi me55 NOME` propone l'album: l'altro tocca **Accetta** e da lì l'album è unico per entrambi (le carte che uno dei due ha contano come prese, ogni spunta vale per tutti). `/esci me55` per uscirne con una copia propria. Dalla Mini App: Impostazioni → Amici, e il pulsante "👥 Condividi" in ogni collezione.

## Da PC (facoltativo)

```bash
git clone <questo repo> && cd BotScraping
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # TELEGRAM_BOT_TOKEN (+ EBAY_CLIENT_ID / EBAY_CLIENT_SECRET)
python cli.py cerca --dry-run   # un giro di ricerca senza notifiche
python cli.py actions           # comandi Telegram + inseguimenti + ricerca, come su GitHub Actions
python -m pytest -q             # test
```

Tutto il resto (checklist, impostazioni, storico, prezzi) si fa da Telegram o dalla Mini App: non c'è più un'interfaccia web locale.

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
(Telegram), `pokebot/telegram_bot.py` (comandi Telegram), `pokebot/watch.py` (inseguimenti), `pokebot/collections.py`
(altre collezioni), `pokebot/webapp_export.py` + `deploy/app/` (Mini App), `.github/workflows/bot.yml` (esecuzione su GitHub Actions),
`deploy/cloudflare-worker.js` + `deploy/wrangler.toml` (ponte Telegram → GitHub), `.github/workflows/ponte.yml` (ne fa il deploy).
