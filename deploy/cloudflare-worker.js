// Ponte Telegram → GitHub Actions (Cloudflare Worker, piano gratuito), pubblicato da .github/workflows/ponte.yml.
// - Ogni messaggio al bot diventa un file cifrato nel branch "bot-queue" e sveglia subito il workflow "PokéBot".
// - Ogni 5 minuti (timer del worker) sveglia il bot solo se c'è qualcosa da fare (vedi timerReason).
// - Serve la Mini App su /app e il suo stato su /api/state (solo i dati di chi la apre, verificati con la firma di Telegram).
//
// Chiavi del worker (le carica ponte.yml): TELEGRAM_BOT_TOKEN, GITHUB_TOKEN (fine-grained, "Contents: Read and write"
// su questo repository), GITHUB_REPO (in wrangler.toml), TELEGRAM_CHAT_ID (facoltativa: il proprietario, prima che
// il bot abbia salvato il suo stato).
// Il repository è pubblico: stato e comandi sono cifrati con AES-GCM, chiave ricavata dal token del bot (come vault.py).

const DEFAULT_REPO = "simonecassu/BotScraping";
const QUEUE_BRANCH = "bot-queue";
// tipi di aggiornamento che Telegram manda al ponte (pre_checkout_query: conferma dei pagamenti in Stars)
const ALLOWED_UPDATES = ["message", "callback_query", "pre_checkout_query"];
const NOT_MEMBER = "non sei ancora tra le persone collegate al bot: scrivi /start al bot per metterti in lista d'attesa";

const repoOf = (env) => env.GITHUB_REPO || DEFAULT_REPO;
const tok = (env) => String(env.TELEGRAM_BOT_TOKEN || "").trim(); // come config.py: un a-capo nel secret non cambia le chiavi
const enc = new TextEncoder();

async function sha256Hex(text) {
  const hash = await crypto.subtle.digest("SHA-256", enc.encode(text));
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// segreti ricavati dal token: niente da configurare a mano
const webhookSecret = async (env) => (await sha256Hex("pokebot-webhook:" + tok(env))).slice(0, 40);
const adminKey = async (env) => (await sha256Hex("pokebot-admin:" + tok(env))).slice(0, 40);
// nome del file di stato di una persona (come vault.file_id): nel branch pubblico non compare il chat id
const fileId = async (env, chatId) => (await sha256Hex(`pokebot-file:${tok(env)}:${chatId}`)).slice(0, 32);

function sameText(a, b) { // confronto a tempo costante
  a = String(a || ""); b = String(b || "");
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

// ---- cifratura (stesso formato di pokebot/vault.py: "PKB1" + nonce di 12 byte + testo cifrato con il tag) ----
const MAGIC = [0x50, 0x4b, 0x42, 0x31];

async function stateKey(env) {
  const raw = await crypto.subtle.digest("SHA-256", enc.encode("pokebot-state-v1:" + tok(env)));
  return crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);
}

async function seal(env, bytes) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await stateKey(env), bytes));
  const out = new Uint8Array(16 + ct.length);
  out.set(MAGIC);
  out.set(iv, 4);
  out.set(ct, 16);
  return out;
}

async function unseal(env, buf) {
  const b = new Uint8Array(buf);
  if (!MAGIC.every((x, i) => b[i] === x)) throw new Error("non cifrato");
  return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: b.slice(4, 16) }, await stateKey(env), b.slice(16)));
}

function base64(bytes) {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

// Un file di stato dal branch bot-state (state.json = riepilogo, state-<impronta>.json = una persona), decifrato.
// Dall'API di GitHub con il token del ponte: sempre aggiornato (niente cache del CDN) e senza i limiti delle letture anonime.
// Il bot risalva lo stato a ogni giro (force-push): per qualche secondo GitHub può non servire ancora il file nuovo.
// Allora si riprova una volta e, se ancora niente, vale l'ultimo stato letto bene (tenuto in memoria per un'ora).
// I byte si prendono dal blob git (API "git/blobs"): l'API dei contenuti scambia i file piccoli per testo e li
// corrompe riscrivendoli in UTF-8, anche in base64; il blob è servito byte per byte.
const lastGood = new Map();
function fromBase64(b64) {
  const bin = atob(b64.replace(/\s+/g, ""));
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
async function readBlob(env, sha) {
  const b = await gh(env, "GET", `/repos/${repoOf(env)}/git/blobs/${sha}`);
  if (!b.ok) throw new Error("blob " + b.status);
  const j = await b.json();
  return fromBase64(j.content);
}
async function readState(env, name = "state.json") {
  const path = `/repos/${repoOf(env)}/contents/${name}.enc?ref=bot-state`;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const r = await gh(env, "GET", path);
      if (r.ok) {
        const meta = await r.json();
        const st = JSON.parse(new TextDecoder().decode(await unseal(env, await readBlob(env, meta.sha))));
        lastGood.set(name, { st, ts: Date.now() });
        return st;
      }
      if (r.status === 401 || r.status === 403) break; // token: inutile riprovare
    } catch {}
    await new Promise((res) => setTimeout(res, 1500));
  }
  const prev = lastGood.get(name);
  return prev && Date.now() - prev.ts < 3600e3 ? prev.st : null;
}

// Chat autorizzate: il proprietario e le persone accettate (dal riepilogo del bot). null se il riepilogo non si legge:
// allora nessuno viene respinto (decide il bot, che conosce le persone collegate).
async function allowedChatIds(env) {
  const st = await readState(env);
  if (!st) return null;
  const ids = new Set();
  if (env.TELEGRAM_CHAT_ID) ids.add(String(env.TELEGRAM_CHAT_ID).trim());
  if (st.owner_chat_id) ids.add(String(st.owner_chat_id));
  for (const c of st.chat_ids || []) ids.add(String(c));
  return ids;
}

// Ogni 5 minuti: sveglia GitHub solo se serve. Senza stato leggibile sveglia comunque, per sicurezza.
function timerReason(st, nowSec) {
  if (!st) return "stato non disponibile";
  if (st.next_watch && Number(st.next_watch) <= nowSec + 60) return "inseguimento da controllare";
  const ch = st.channel || {};
  if (ch.set) { // canale degli affari: il post del giorno è dovuto
    const at = new Date(nowSec * 1000);
    const hour = Number(new Intl.DateTimeFormat("it-IT", { hour: "numeric", hour12: false, timeZone: "Europe/Rome" }).format(at));
    const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Rome" }).format(at); // YYYY-MM-DD
    if (hour >= Number(ch.hour ?? 19) && ch.last !== today) return "post del canale dovuto";
  }
  if (nowSec - Number(st.last_search_ts || 0) >= Number(st.interval_minutes || 20) * 60 - 30) return "ricerca completa dovuta";
  if (Number(st.queued || 0) > 0) return "annunci da inviare";
  return null;
}

async function telegram(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${tok(env)}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

// Se il webhook è stato registrato con un elenco vecchio (senza pagamenti), lo aggiorna mantenendo URL e segreto.
async function ensureWebhook(env) {
  const info = await telegram(env, "getWebhookInfo", {});
  const w = (info && info.result) || {};
  if (!w.url) return;
  const have = w.allowed_updates || [];
  if (ALLOWED_UPDATES.every((u) => have.includes(u))) return;
  await telegram(env, "setWebhook", { url: w.url, secret_token: await webhookSecret(env), allowed_updates: ALLOWED_UPDATES, drop_pending_updates: false });
}

async function gh(env, method, path, body, accept = "application/vnd.github+json") {
  return fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: accept,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pokebot-bridge",
      "Content-Type": "application/json",
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}

async function ensureQueueBranch(env) {
  const repo = repoOf(env);
  if ((await gh(env, "GET", `/repos/${repo}/git/ref/heads/${QUEUE_BRANCH}`)).ok) return;
  const info = await (await gh(env, "GET", `/repos/${repo}`)).json();
  const base = await (await gh(env, "GET", `/repos/${repo}/git/ref/heads/${info.default_branch}`)).json();
  await gh(env, "POST", `/repos/${repo}/git/refs`, { ref: `refs/heads/${QUEUE_BRANCH}`, sha: base.object.sha });
}

// Salva il comando, cifrato, come file su GitHub (un file per comando: nessun conflitto), così il bot lo esegue al
// prossimo giro anche se GitHub cancella un run in attesa doppio. True se salvato.
async function enqueue(env, cmd) {
  const repo = repoOf(env);
  const name = `queue/${Date.now()}-${Math.random().toString(36).slice(2, 8)}.json`;
  const body = { message: "comando", branch: QUEUE_BRANCH, content: base64(await seal(env, enc.encode(JSON.stringify(cmd)))) };
  let r = await gh(env, "PUT", `/repos/${repo}/contents/${name}`, body);
  if (r.status === 404 || r.status === 422) {
    await ensureQueueBranch(env);
    r = await gh(env, "PUT", `/repos/${repo}/contents/${name}`, body);
  }
  return r.ok;
}

const dispatch = (env, payload) => gh(env, "POST", `/repos/${repoOf(env)}/dispatches`, payload);

// Mette in coda e sveglia il bot. True se il comando è salvato (il bot lo esegue comunque al prossimo giro).
async function sendCommand(env, chatId, text, name, extra) {
  const cmd = { chat_id: String(chatId), text, ts: Date.now() / 1000, name: name || "", ...(extra || {}) };
  if (!(await enqueue(env, cmd))) return false;
  await dispatch(env, { event_type: "telegram" });
  return true;
}

// Diagnosi (per il log del deploy): cosa risponde GitHub al ponte e se il riepilogo si decifra. Nessun dato personale.
async function diag(env) {
  const out = { token: tok(env).length, repo: repoOf(env) };
  try {
    const r = await gh(env, "GET", `/repos/${repoOf(env)}/contents/state.json.enc?ref=bot-state`);
    out.contents_http = r.status;
    const meta = await r.json();
    out.size = meta.size; out.message = meta.message;
    if (meta.sha) {
      const bytes = await readBlob(env, meta.sha);
      out.bytes = bytes.length; out.magic = String.fromCharCode(...bytes.slice(0, 4));
      try { const st = JSON.parse(new TextDecoder().decode(await unseal(env, bytes))); out.decrypt = "ok"; out.chat_ids = (st.chat_ids || []).length; out.generated_at = st.generated_at; }
      catch (e) { out.decrypt = "ERRORE " + (e && e.message); }
    }
  } catch (e) { out.error = String(e && e.message || e); }
  out.lastGood = [...lastGood.keys()];
  try {  // l'indice delle carte servito con la Mini App
    const a = await env.ASSETS.fetch(new Request("https://x/cards.json"));
    out.cards_json = { http: a.status, bytes: (await a.arrayBuffer()).byteLength, type: a.headers.get("content-type") };
  } catch (e) { out.cards_json = String(e && e.message || e); }
  return out;
}

// Apertura della Mini App: una sveglia del bot (non più di una al minuto per istanza del worker, non serve di più).
let lastAppWake = 0;
async function wakeForApp(env) {
  if (Date.now() - lastAppWake < 60e3) return;
  lastAppWake = Date.now();
  try { await dispatch(env, { event_type: "timer", client_payload: { reason: "apertura della Mini App" } }); } catch {}
}

// Chi non è collegato può solo chiedere l'accesso: lo stesso messaggio una volta ogni 30 minuti, e non più di 50
// richieste di sconosciuti ogni 30 minuti (per istanza del worker), così nessuno può far partire GitHub a raffica.
// Un "/start CODICE" d'invito è un messaggio diverso dal "/start" di prima: passa.
const strangerSeen = new Map();
function strangerAllowed(chatId, text, nowMs) {
  for (const [k, t] of strangerSeen) if (nowMs - t > 30 * 60 * 1000) strangerSeen.delete(k);
  const key = chatId + " " + text.trim().toLowerCase();
  if (strangerSeen.has(key) || strangerSeen.size >= 50) return false;
  strangerSeen.set(key, nowMs);
  return true;
}

function page(title, body) {
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><body style="font:17px system-ui;padding:24px;max-width:640px;margin:auto">
<h2>${title}</h2>${body}</body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}

async function memberFrom(env, request) {
  let body = {};
  try { body = await request.json(); } catch {}
  const user = await verifyInitData(env, body.initData);
  if (!user) return { error: Response.json({ ok: false, error: "non autenticato: riapri l'app da Telegram" }, { status: 401 }) };
  const ids = await allowedChatIds(env);
  if (!ids) return { error: Response.json({ ok: false, error: "il bot si sta aggiornando: riprova tra un minuto" }, { status: 503 }) };
  if (!ids.has(String(user.id))) return { error: Response.json({ ok: false, error: NOT_MEMBER }, { status: 403 }) };
  return { user, body };
}

async function hmac(keyBytes, message) {
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, enc.encode(message)));
}

// Verifica la firma di Telegram sui dati della Mini App; restituisce l'utente o null.
async function verifyInitData(env, initData) {
  if (!initData) return null;
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;
  params.delete("hash");
  const dataCheck = [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("\n");
  const secret = await hmac(enc.encode("WebAppData"), tok(env));
  const expected = [...(await hmac(secret, dataCheck))].map((b) => b.toString(16).padStart(2, "0")).join("");
  if (!sameText(expected, hash)) return null;
  const authDate = Number(params.get("auth_date") || 0);
  if (!authDate || Date.now() / 1000 - authDate > 86400) return null; // dati più vecchi di un giorno
  try {
    return JSON.parse(params.get("user") || "null");
  } catch {
    return null;
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    // Mini App: pagina statica
    if (url.pathname === "/app" || url.pathname.startsWith("/app/")) {
      // la radice degli asset serve index.html senza redirect (un /index.html esplicito verrebbe rediretto a "/")
      const path = url.pathname === "/app" || url.pathname === "/app/" ? "/" : url.pathname.slice(4);
      const res = await env.ASSETS.fetch(new Request(new URL(path, url.origin), { headers: request.headers }));
      if (res.status >= 300 && res.status < 400) {
        return env.ASSETS.fetch(new Request(new URL("/", url.origin), { headers: request.headers }));
      }
      return res;
    }
    // Mini App: lo stato di chi la apre (firmato da Telegram); fuori da Telegram niente dati
    if (url.pathname === "/api/state") {
      if (request.method !== "POST") return Response.json({ per_user: true });
      const m = await memberFrom(env, request);
      if (m.error) return m.error;
      const st = await readState(env, `state-${await fileId(env, m.user.id)}.json`);
      if (m.body.wake) await wakeForApp(env);  // all'apertura: il bot rifà il giro e ripubblica dati freschi
      if (!st) return Response.json({ ok: false, error: "il bot sta preparando i tuoi dati: riprova tra un minuto" }, { status: 404 });
      return Response.json(st, { headers: { "Cache-Control": "no-store" } });
    }
    // Mini App: comando di una persona collegata (firmato da Telegram)
    if (url.pathname === "/api/cmd" && request.method === "POST") {
      const m = await memberFrom(env, request);
      if (m.error) return m.error;
      const text = String(m.body.text || "").trim().slice(0, 4000);
      if (!text.startsWith("/")) return Response.json({ ok: false, error: "comando non valido" }, { status: 400 });
      const ok = await sendCommand(env, m.user.id, text, m.user.first_name || m.user.username || "");
      return Response.json(ok ? { ok } : { ok, error: "il bot non risponde adesso: riprova tra un minuto" }, { status: ok ? 200 : 502 });
    }

    if (request.method === "GET") {
      if (!tok(env) || !env.GITHUB_TOKEN) return page("⚠️ Ponte non configurato", "<p>Esegui il workflow «Ponte Telegram».</p>");
      // /setup, /reset e /diag solo con la chiave (la manda ponte.yml): nessun altro può spostare il webhook
      if (url.pathname === "/setup" || url.pathname === "/reset" || url.pathname === "/diag") {
        if (!sameText(request.headers.get("X-Pokebot-Key"), await adminKey(env))) return new Response("forbidden", { status: 403 });
        if (url.pathname === "/diag") return Response.json(await diag(env));
        if (url.pathname === "/reset") {
          const res = await telegram(env, "deleteWebhook", { drop_pending_updates: false });
          return page(res.ok ? "Ponte disattivato" : "❌ Errore", `<pre>${JSON.stringify(res, null, 2)}</pre>`);
        }
        const res = await telegram(env, "setWebhook", {
          url: `${url.origin}/`, secret_token: await webhookSecret(env), allowed_updates: ALLOWED_UPDATES, drop_pending_updates: false,
        });
        // pulsante "App" accanto alla chat che apre la Mini App
        await telegram(env, "setChatMenuButton", { menu_button: { type: "web_app", text: "App", web_app: { url: `${url.origin}/app` } } });
        return page(res.ok ? "✅ Ponte attivo" : "❌ Errore", res.ok ? "<p>Ogni messaggio al bot lo sveglia subito.</p>" : `<pre>${JSON.stringify(res, null, 2)}</pre>`);
      }
      const info = await telegram(env, "getWebhookInfo");
      const active = info.ok && info.result && info.result.url === `${url.origin}/`;
      return page(active ? "✅ Ponte attivo" : "Ponte non attivo", active ? "" : "<p>Esegui il workflow «Ponte Telegram».</p>");
    }

    if (request.method !== "POST") return new Response("ok");
    if (!sameText(request.headers.get("X-Telegram-Bot-Api-Secret-Token"), await webhookSecret(env))) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
    }
    // pagamento in Stars: la conferma va data entro 10 secondi, quindi la dà il ponte
    if (update.pre_checkout_query) {
      const q = update.pre_checkout_query;
      const ok = q.currency === "XTR" && String(q.invoice_payload || "").startsWith("pro:");
      await telegram(env, "answerPreCheckoutQuery", ok ? { pre_checkout_query_id: q.id, ok: true }
        : { pre_checkout_query_id: q.id, ok: false, error_message: "Pagamento non riconosciuto." });
      return new Response("ok");
    }
    if (update.message && update.message.successful_payment) {
      const m = update.message, p = m.successful_payment;
      const payment = { currency: p.currency, total_amount: p.total_amount, invoice_payload: p.invoice_payload,
        telegram_payment_charge_id: p.telegram_payment_charge_id, subscription_expiration_date: p.subscription_expiration_date || 0,
        is_recurring: !!p.is_recurring, is_first_recurring: !!p.is_first_recurring };
      const frm = m.from || {};
      // un pagamento non si perde: se GitHub non risponde, Telegram ripete l'aggiornamento più tardi
      if (!(await sendCommand(env, String(m.chat.id), "/pagamento", frm.first_name || frm.username || "", { payment }))) {
        return new Response("riprova", { status: 503 });
      }
      await telegram(env, "sendMessage", { chat_id: m.chat.id, text: "⭐ Pagamento ricevuto, grazie! Attivo tutto: conferma tra circa un minuto." });
      return new Response("ok");
    }
    // messaggio normale oppure pulsante toccato (callback_query: il dato del pulsante è un comando)
    let msg = update.message;
    let text = msg && msg.text;
    if (update.callback_query) {
      const cb = update.callback_query;
      await telegram(env, "answerCallbackQuery", { callback_query_id: cb.id });
      msg = cb.message;
      text = cb.data;
    }
    if (!msg || !text || (msg.chat && msg.chat.type !== "private")) return new Response("ok");
    const chatId = String(msg.chat.id);
    const frm = (update.callback_query ? update.callback_query.from : msg.from) || {};
    const ids = await allowedChatIds(env);
    const known = !ids || ids.has(chatId);
    if (!known) { // sconosciuto: solo /start va al bot (lista d'attesa), il resto si ferma qui
      const isStart = /^\/start(@\w+)?(\s|$)/i.test(text.trim());
      if (!isStart || !strangerAllowed(chatId, text, Date.now())) {
        await telegram(env, "sendMessage", { chat_id: chatId, text: isStart
          ? "👋 Richiesta ricevuta: ti avviso io appena l'accesso viene attivato."
          : "👋 Pokébot è su invito. Scrivi /start per metterti in lista d'attesa: ti avviso io appena l'accesso viene attivato." });
        return new Response("ok");
      }
    }
    const ok = await sendCommand(env, chatId, text, frm.first_name || frm.username || "");
    const ack = !ok ? "⚠️ Non riesco a passare il comando al bot in questo momento: riprova tra qualche minuto."
      : known ? "⏳ Ricevuto, avvio il bot: risposta tra circa un minuto."
      : "👋 Benvenuto! Un momento, ti registro: conferma tra circa un minuto.";
    await telegram(env, "sendMessage", { chat_id: chatId, text: ack });
    return new Response("ok");
  },

  // Timer (vedi [triggers] in wrangler.toml): sveglia il bot su GitHub quando c'è qualcosa da fare.
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      try { await ensureWebhook(env); } catch {}
      const reason = timerReason(await readState(env), event.scheduledTime / 1000);
      if (reason) await dispatch(env, { event_type: "timer", client_payload: { reason } });
    })());
  },
};
