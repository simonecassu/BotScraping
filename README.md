# Pokébot

Bot Telegram (con Mini App) che cerca su **Wallapop, Vinted ed eBay.it** le carte Pokémon che mancano alla tua
collezione e ti avvisa appena spunta un annuncio, con prezzo, foto e confronto con il valore **Cardmarket**.
Parte con la *30th Celebration* (più la *Classic Collection*) e segue qualsiasi altra collezione del catalogo pubblico.

Gira gratis su **GitHub Actions** e risponde subito grazie a un piccolo **ponte su Cloudflare Workers**: niente PC acceso.

## Come lo usa una persona

1. Apre il bot e preme **Avvia**: finisce in lista d'attesa; il proprietario la accetta con `/approva`.
2. Una breve introduzione: prima segna nell'app le carte che ha, poi accende le notifiche.
3. **5 giorni di prova** con tutto; poi gratis in versione **Light** oppure l'abbonamento completo in **Telegram Stars**.

| | Completo (prova e abbonamento) | Light (gratis) |
|---|---|---|
| Notifiche | appena esce un annuncio | un riepilogo al giorno, alle 19 |
| Inseguimenti (`/insegui`) | fino a 10 carte, controllo ogni 5 minuti | una carta, controllo ogni 2 ore |
| Collezioni | senza limiti | 5, di cui 3 con la ricerca accesa |

Abbonamento: 250 Stars ogni 30 giorni (`/abbonati`). Rimborso intero entro 3 giorni dal pagamento
(`/rimborsa ID`, solo il proprietario: Telegram non permette rimborsi parziali); dopo si ferma solo il rinnovo.

`/aiuto` nel bot elenca tutti i comandi; il proprietario vede anche i suoi (`/attesa`, `/approva`, `/utenti`,
`/piano`, `/espelli`, `/rimborsa`, `/canale`, `/fonti`, `/intervallo`…). Nessun comando del proprietario
funziona per gli altri, e nessuno vede le richieste di accesso tranne il proprietario.

### Mini App

Il pulsante **App** accanto alla chat apre: **Collezioni** (checklist con le carte possedute accese e le mancanti
al buio, inseguimenti, annunci trovati, prezzi e lista della spesa), **Amici** (codice amico, album in comune),
**Impostazioni**, **Segnalazioni** e **Analisi IA** (in arrivo). Ogni tocco diventa un comando per il bot: il ponte
conferma subito in chat («📲 Ricevuto dalla Mini App»), il bot lo esegue e pubblica lo stato prima della ricerca, così
l'app si aggiorna da sola in circa mezzo minuto e in chat arriva il risultato.
La casella di ricerca in Collezioni trova anche le carte in tutte le espansioni (indice `deploy/app/cards.json`,
generato da `scripts/build_card_index.py` a ogni deploy del ponte), raggruppate per espansione.
Ognuno vede solo i suoi dati.

**Segnalazioni**: ogni utente può scrivere una volta al giorno un problema o un'idea (`/segnala testo`, anche su più
righe, o dalla Mini App); il proprietario riceve un messaggio e le trova tutte con `/segnalazioni` o nella Mini App,
dalle più recenti, con il contatore delle nuove.

### Canale degli affari

Con `/canale @nomecanale` (bot amministratore del canale) il bot pubblica ogni giorno alle 19 le 3 migliori
occasioni, giudicate sul valore Cardmarket: 🔥 affare, 💰 ottimo prezzo, 👍 sotto il valore, 📌 vicino al valore
(fino al 30% sopra). Il post esce anche quando non ci sono affari veri, ma ogni voce dice onestamente di che tipo è;
un annuncio già pubblicato non torna il giorno dopo.

## Prezzi: Cardmarket come riferimento

Il valore di ogni carta è il **trend Cardmarket** (se manca, la media degli ultimi giorni), letto una volta al giorno dall'API gratuita di
[TCGdex](https://tcgdex.dev) (Cardmarket blocca la lettura diretta). Avviso 🔥 *affare* quando un annuncio costa
meno del 70% del trend e meno del minimo Cardmarket (`/affari 70`, modificabile). Le carte senza prezzo Cardmarket
(es. appena uscite) usano i prezzi degli annunci visti dal bot.

## Come riconosce le carte

Titolo e descrizione vengono normalizzati e si cercano numeri `n/128`, nomi delle carte (dal più lungo, così
"Mew ex" non fa scattare "Mew"), nomi alternativi da `data/aliases.json`. Un annuncio è un **lotto** se lo dice
(lotto, bundle, `x5`, `20 carte`) o se contiene più carte: si notifica solo se almeno il 50% sono mancanti (`/soglia`).
Si scartano aste, "cerco/compro", scambi, venduti, proxy, sigillati, accessori e annunci in altre lingue (`/lingua tutte` per accettarli).

| Fonte | Come | Note |
|---|---|---|
| Wallapop | API JSON del sito | solo Italia, esclusi riservati e venduti |
| Vinted | pagina di ricerca con sessione anonima | |
| eBay.it | API Browse ufficiale | solo *Compralo Subito*; serve `EBAY_CLIENT_ID/SECRET` ([chiavi gratuite](https://developer.ebay.com/my/keys)) |

## Installazione (una volta)

Tutto si fa dal browser, anche da iPhone. Secret del repository in
*Settings → Secrets and variables → Actions*:

| Secret | Cos'è |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token di @BotFather |
| `BRIDGE_GITHUB_TOKEN` | [token GitHub fine-grained](https://github.com/settings/personal-access-tokens/new) solo su questo repository, *Contents: Read and write* |
| `CLOUDFLARE_API_TOKEN` | [token Cloudflare](https://dash.cloudflare.com/profile/api-tokens) dal modello *Edit Cloudflare Workers* |
| `CLOUDFLARE_ACCOUNT_ID` | Account ID (pagina *Workers & Pages*) |
| `TELEGRAM_CHAT_ID` | facoltativo: il tuo chat id (il ponte ti riconosce anche prima del primo giro) |
| `EBAY_CLIENT_ID`, `EBAY_CLIENT_SECRET` | facoltativi: per cercare anche su eBay |

1. Actions → **Ponte Telegram** → *Run workflow*: pubblica il ponte e collega il webhook (alla fine: "Ponte attivo").
2. Actions → **PokéBot** → *Run workflow*: primo giro.
3. Scrivi `/start` al bot: il primo che lo fa diventa il proprietario.

GitHub sospende i workflow pianificati dopo 60 giorni senza attività sul repository: arriva un'email e si riattiva con un tap.

### Come gira

- Il **ponte** (`deploy/cloudflare-worker.js`) riceve i messaggi da Telegram, li salva come file cifrati nel branch
  `bot-queue` e sveglia subito il workflow. Ogni 5 minuti controlla se c'è qualcosa da fare (ricerca completa ogni
  `/intervallo` minuti, inseguimenti, annunci da consegnare, post del canale) e solo allora sveglia GitHub.
  Il cron di GitHub in `bot.yml` è una riserva oraria.
- Il **bot** gira in due fasi (`python cli.py actions --fase comandi`, poi `--fase ricerca`): prima i comandi in coda,
  subito salvati nel branch `bot-state` (`scripts/save_state.sh`, così la Mini App li vede in mezzo minuto), poi
  inseguimenti, prezzi, ricerca e consegne, e un secondo salvataggio.
- **Novità in chat**: quando cambia il testo di `data/novita.txt` (poche righe su cosa c'è di nuovo), il bot lo manda
  una volta a tutte le persone collegate, di giorno; file vuoto, nessun messaggio.
- Chi non è collegato può solo chiedere l'accesso (`/start`; lo stesso messaggio al massimo una volta ogni 30 minuti).
- I comandi escono dalla coda solo dopo che lo stato è stato salvato: se un giro si interrompe, il successivo li ritrova
  (un pagamento ripetuto si riconosce e non conta due volte).

### Privacy e cifratura

Il repository è pubblico, quindi tutto ciò che il bot salva nei branch (`bot-state`: database e stato della Mini App;
`bot-queue`: comandi in arrivo) è **cifrato con AES-GCM**, con una chiave ricavata da `TELEGRAM_BOT_TOKEN`
(`pokebot/vault.py`, lo stesso formato nel ponte); anche i nomi dei file sono impronte, non chat id. La Mini App
riceve dal ponte solo i dati di chi la apre, dopo aver verificato la firma di Telegram, e di ogni annuncio solo le
carte che mancano a lei. Nei log di GitHub Actions i chat id compaiono accorciati.

**Attenzione**: se cambi il token del bot, lo stato salvato non si legge più e il workflow si ferma al ripristino
(senza sovrascrivere nulla). Prima di cambiarlo scarica il database in chiaro da un PC
(`python cli.py unseal pokebot.db.enc pokebot.db` con il vecchio token), poi ricifralo con il nuovo
(`python cli.py seal pokebot.db pokebot.db.enc`) e ripubblica il ponte.

## Da PC (sviluppo)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env              # TELEGRAM_BOT_TOKEN (+ eBay)
python cli.py cerca --dry-run     # un giro di ricerca senza notifiche
python cli.py analizza "Lapras 131/128 30th"   # come il bot classifica un testo
python cli.py export-json /tmp/stato --chiaro  # lo stato della Mini App, leggibile
python -m pytest -q
```

Struttura: `pokebot/cards.py` (collezioni e indice), `matcher.py` (riconoscimento, singola o lotto),
`scrapers/` (un modulo per marketplace), `search.py` (ciclo di ricerca e consegne), `watch.py` (inseguimenti),
`telegram_bot.py` (comandi), `plans.py` (prova, Light, abbonamenti), `cardmarket.py` (prezzi TCGdex),
`channel.py` (canale degli affari), `collections.py` (altre collezioni), `webapp_export.py` + `deploy/app/` (Mini App),
`vault.py` + `queue.py` (cifratura e coda comandi), `news.py` (novità in chat), `deploy/` (ponte), `.github/workflows/` (bot, ponte, video demo),
`promo/` (volantino stampabile).

I dati della 30th (`data/sets/`) vengono da [pokemon-tcg-data](https://github.com/PokemonTCG/pokemon-tcg-data):
per aggiornarli `python scripts/import_set.py me55 --printed-total 128` (le parti scritte a mano restano).
