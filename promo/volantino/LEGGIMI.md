# Volantino A4

- `../volantino-pokebot-A4.pdf` è pronto da stampare (A4, senza margini: in stampa scegli "Adatta alla pagina" se la stampante taglia i bordi).
- Il QR grande e le linguette portano a `t.me/Scraperamemebot?start=volantino`: chi arriva da qui compare in lista d'attesa "da volantino" (lo accetti tu con `/approva`).
- Il QR piccolo porta al canale `@scraperofferta3`.
- Per rigenerarlo dopo una modifica a `volantino.html`:
  `NODE_PATH=<cartella node_modules con playwright> node promo/volantino/stampa.cjs $PWD/promo/volantino/volantino.html $PWD/promo/volantino-pokebot-A4.pdf /tmp/anteprima.png`
- Font Nunito, © The Nunito Project Authors, con licenza SIL Open Font License 1.1 (testo in `OFL.txt`).
