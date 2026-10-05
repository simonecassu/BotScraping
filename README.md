# PokéBot 30th — bot per completare il master set *Pokémon 30th Celebration*

Cerca in automatico su **Subito.it, Vinted ed eBay.it** le carte che ti mancano del set
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

## Avvio rapido

```bash
git clone <questo repo> && cd BotScraping
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # inserisci TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
python run.py               # interfaccia su http://localhost:8080
```

Con Docker: `cp .env.example .env`, compila il file, poi `docker compose up -d`.

### Telegram in 2 minuti
1. Su Telegram scrivi a **@BotFather** → `/newbot` → copia il token in `TELEGRAM_BOT_TOKEN`.
2. Scrivi un messaggio qualsiasi al tuo nuovo bot.
3. Apri `https://api.telegram.org/bot<TOKEN>/getUpdates` e copia il valore di `"chat":{"id":…}` in `TELEGRAM_CHAT_ID`.
4. In **Impostazioni → Invia messaggio di prova** verifichi che arrivi.

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
| Subito.it | API JSON del sito (`hades.subito.it`) | solo annunci in vendita, ordinati per data |
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
(Telegram), `pokebot/scheduler.py` (esecuzione periodica), `pokebot/web/` (interfaccia Flask).
