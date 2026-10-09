/* Registra un giro nella Mini App (dati dimostrativi) come video verticale a tutto schermo.
 *   NODE_PATH=... node scripts/record_demo.cjs <cartella demo con state-1.json> <cartella output>
 * L'app gira in un finto Telegram (i comandi rispondono ok), ingrandita a 1080×1920 con un pallino sui tocchi. */
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const [demoDir, outDir] = process.argv.slice(2);
const APP = path.resolve(__dirname, "..", "deploy", "app");
const ORIGIN = "https://pokebot.demo";
const W = 1080, H = 1920, CSS_W = 400;            // telefono largo 400 px "veri", ingrandito ×2.7
const ZOOM = W / CSS_W;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (m) => console.log(new Date().toISOString().slice(11, 19), m);

const FAKE_TG = `window.Telegram = { WebApp: { initData: "demo", initDataUnsafe: { user: { id: 1, first_name: "Simone" } },
  ready() {}, expand() {}, openLink() {}, openTelegramLink() {}, switchInlineQuery() {}, showAlert() {}, HapticFeedback: { impactOccurred() {} },
  BackButton: { onClick() {}, show() {}, hide() {} }, MainButton: { show() {}, hide() {} } } };`;

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
    viewport: { width: W, height: H }, deviceScaleFactor: 1, hasTouch: true, locale: "it-IT",
    recordVideo: { dir: outDir, size: { width: W, height: H } },
  });
  await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: ORIGIN });
  const page = await ctx.newPage();
  page.setDefaultTimeout(20000);
  page.on("dialog", (d) => d.dismiss().catch(() => {}));
  await page.route(`${ORIGIN}/**`, async (route) => {
    const u = new URL(route.request().url());
    if (u.pathname.startsWith("/api/state")) return route.fulfill({ path: path.join(demoDir, "state-1.json"), contentType: "application/json" });
    if (u.pathname === "/api/cmd") { await sleep(250); return route.fulfill({ json: { ok: true, status: 204 } }); }
    const rel = u.pathname === "/app" || u.pathname === "/app/" ? "index.html" : u.pathname.replace(/^\/app\//, "");
    const file = path.join(APP, rel);
    if (fs.existsSync(file)) return route.fulfill({ path: file });
    return route.fulfill({ status: 404, body: "" });
  });
  await page.route("https://telegram.org/**", (route) => route.fulfill({ contentType: "application/javascript", body: FAKE_TG }));
  await page.addInitScript(() => {
    // pallino dove tocca il dito: fuori dal body ingrandito, in pixel veri dello schermo
    window.__tap = (x, y) => {
      const d = document.createElement("div");
      d.style.cssText = `position:fixed;left:${x - 45}px;top:${y - 45}px;width:90px;height:90px;border-radius:50%;` +
        "background:rgba(255,255,255,.45);border:4px solid rgba(255,255,255,.9);z-index:99999;pointer-events:none;" +
        "transform:scale(.4);opacity:1;transition:transform .35s ease-out,opacity .5s ease-out .25s";
      document.documentElement.appendChild(d);
      requestAnimationFrame(() => { d.style.transform = "scale(1)"; d.style.opacity = "0"; });
      setTimeout(() => d.remove(), 900);
    };
  });
  await page.goto(`${ORIGIN}/app/`, { waitUntil: "domcontentloaded" });
  await page.addStyleTag({ content: `body { zoom: ${ZOOM}; } * { cursor: none !important; } html { scroll-behavior: smooth; } #upd { visibility: hidden; }` });
  await page.waitForSelector("#t-coll:not(:empty)");
  log("home pronta");
  await sleep(2500);

  const tapEl = async (loc, ms = 1500) => {
    await loc.scrollIntoViewIfNeeded();
    const b = await loc.boundingBox();
    if (b) { await page.evaluate(([x, y]) => window.__tap(x, y), [b.x + b.width / 2, b.y + b.height / 2]); await sleep(220); }
    await loc.tap();
    await sleep(ms);
  };
  const tap = (sel, ms) => { log("tocco " + sel); return tapEl(page.locator(sel).first(), ms); };
  const scroll = async (dy, ms = 1300) => { await page.evaluate((d) => window.scrollBy(0, d), dy); await sleep(ms); };
  const top = async (ms = 700) => { await page.evaluate(() => window.scrollTo(0, 0)); await sleep(ms); };
  const type = async (sel, text) => { await tap(sel, 300); await page.locator(sel).first().pressSequentially(text, { delay: 160 }); await sleep(500); };

  // 1. Collezioni → 30th
  await tap(".tile[data-go=coll]", 2200);
  await tap(".coll[data-go=home]", 2200);
  // 2. Checklist: scorre, tre carte trovate si accendono
  await scroll(650, 1500);
  const miss = page.locator("#grid .c.miss");
  for (let i = 0; i < 3; i++) await tapEl(miss.nth(i), 1200);
  await scroll(500, 1200);
  // 3. Doppioni: un tocco segna una copia in più
  await top();
  await tap("#dupmode", 1200);
  await tapEl(page.locator("#grid .c:not(.miss)").nth(4), 1400);
  await tap("#dupmode", 800);
  // 4. Condividi album con un'amica
  await tap("#sharebtn", 2200);
  // 5. Inseguimento: scrive 151 e lo avvia, poi apre gli annunci della carta inseguita
  await tap("#cseg button[data-cseg=chase]", 1500);
  await type("#chase-card", "151");
  await tap("#chase-go", 2000);
  const folder = page.locator("#chase-found .folder .hd").first();
  if (await folder.count()) await tapEl(folder, 2200);
  // 6. Trovati: annunci, prezzi, lista della spesa
  await top();
  await tap("#cseg button[data-cseg=found]", 1600);
  const f2 = page.locator("#folders .folder .hd").first();
  if (await f2.count()) await tapEl(f2, 2000);
  await top();
  await tap(".seg button[data-seg=prz]", 2200);
  await tap(".seg button[data-seg=spesa]", 2000);
  await scroll(500, 1600);
  // 7. Impostazioni: un interruttore e gli amici
  await top();
  await tap("#back", 900);
  await tap("#back", 1200);
  await tap(".tile[data-go=settings]", 1800);
  await tapEl(page.locator("#settings .sw").nth(2), 1500);
  await tap("[data-cmd='/amico']", 1800);
  await type("#friendcode", "GX7K2P");
  await tap("#friendgo", 2200);
  await top();
  await tap("#back", 2500);

  const video = page.video();
  log("chiudo e salvo il video");
  await ctx.close();
  const webm = await video.path();
  fs.renameSync(webm, path.join(outDir, "pokebot-demo.webm"));
  log("video: " + path.join(outDir, "pokebot-demo.webm"));
}
