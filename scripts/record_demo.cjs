/* Registra un giro nella Mini App (dati dimostrativi) come video verticale da telefono.
 *   NODE_PATH=... node scripts/record_demo.cjs <cartella demo con state-1.json> <cartella output>
 * Serve l'app da deploy/app intercettando le richieste: niente server, niente ponte. */
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const [demoDir, outDir] = process.argv.slice(2);
const APP = path.resolve(__dirname, "..", "deploy", "app");
const ORIGIN = "https://pokebot.demo";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const log = (m) => console.log(new Date().toISOString().slice(11, 19), m);

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch();
  try {
    await record(browser);
  } finally {
    await browser.close().catch(() => {});  // mai lasciare Chromium vivo: terrebbe aperto il passo su GitHub Actions
  }
})().catch((e) => { console.error(e); process.exit(1); });

async function record(browser) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true, locale: "it-IT",
    recordVideo: { dir: outDir, size: { width: 1080, height: 2338 } },
  });
  const page = await ctx.newPage();
  await page.route(`${ORIGIN}/**`, async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname.startsWith("/api/state")) return route.fulfill({ path: path.join(demoDir, "state-1.json"), contentType: "application/json" });
    if (u.pathname === "/api/cmd") return route.fulfill({ json: { ok: true, status: 204 } });
    const rel = u.pathname === "/app" || u.pathname === "/app/" ? "index.html" : u.pathname.replace(/^\/app\//, "");
    const file = path.join(APP, rel);
    if (fs.existsSync(file)) return route.fulfill({ path: file });
    return route.fulfill({ status: 404, body: "" });
  });
  await page.route("https://telegram.org/**", (route) => route.fulfill({ contentType: "application/javascript", body: "" }));
  // dentro Telegram la Mini App non mostra il titolo del browser: nascondo il cursore e tolgo le barre
  page.setDefaultTimeout(20000);
  await page.goto(`${ORIGIN}/app/`, { waitUntil: "domcontentloaded" });
  await page.addStyleTag({ content: "* { cursor: none !important }" });
  await page.waitForSelector("#t-coll:not(:empty)");
  log("home pronta");
  await sleep(2600);

  const tap = async (sel, ms = 1800) => { log("tocco " + sel); await page.locator(sel).first().tap(); await sleep(ms); };
  await tap(".tile[data-go=coll]", 2600);
  await tap(".coll[data-go=home]", 3000);                 // checklist: accese = ho, buie = mancano
  await page.mouse.wheel(0, 500); await sleep(1600);
  const miss = page.locator("#grid .c.miss");
  for (let i = 0; i < 3; i++) { await miss.nth(i).tap(); await sleep(1300); }   // tre carte trovate: si accendono
  await sleep(800);
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" })); await sleep(900);
  await tap("#cseg button[data-cseg=chase]", 2600);       // inseguimento in corso
  const chase = page.locator("#chase-list .folder [data-open], #chase-list .folder").first();
  if (await chase.count()) { await chase.tap(); await sleep(2600); }
  await page.evaluate(() => window.scrollTo({ top: 0, behavior: "smooth" })); await sleep(600);
  await tap("#cseg button[data-cseg=found]", 2200);       // annunci trovati
  const folder = page.locator("#folders .folder .hd").first();
  if (await folder.count()) { await folder.tap(); await sleep(2200); }
  await tap(".seg button[data-seg=prz]", 2600);           // prezzi e quanto manca
  await tap(".seg button[data-seg=spesa]", 3000);         // lista della spesa
  await page.mouse.wheel(0, 400); await sleep(1800);
  await tap("#back", 1000);
  await tap("#back", 1200);
  await tap(".tile[data-go=settings]", 2800);
  await sleep(600);
  const video = page.video();
  log("chiudo e salvo il video");
  await ctx.close();
  const webm = await video.path();
  fs.renameSync(webm, path.join(outDir, "pokebot-demo.webm"));
  log("video: " + path.join(outDir, "pokebot-demo.webm"));
}
