// Ponte Telegram → GitHub Actions (Cloudflare Worker, piano gratuito).
// Ogni messaggio inviato al bot avvia subito il workflow "PokéBot 30th" passando il comando;
// inoltre, ogni 5 minuti (timer del worker), sveglia il bot: inseguimenti /insegui e, quando è ora, la ricerca completa.
//
// Variabili da impostare nel Worker (Settings → Variables and Secrets, tipo "Secret"):
//   TELEGRAM_BOT_TOKEN  token di @BotFather
//   GITHUB_TOKEN        fine-grained token con permesso "Contents: Read and write" sul repository
//   GITHUB_REPO         (facoltativo) default simonecassu/BotScraping
//   TELEGRAM_CHAT_ID    (facoltativo) accetta solo questa chat
//
// Dopo il deploy apri UNA volta in Safari:  https://<indirizzo-del-worker>/setup
// Il worker registra da solo il webhook su Telegram e il pulsante "App" (Mini App servita su /app).
// Apri /status per controllare, /reset per tornare alla modalità base.

const DEFAULT_REPO = "simonecassu/BotScraping";

async function secretFor(env) {
  // segreto del webhook derivato dal token: niente da configurare a mano
  const data = new TextEncoder().encode("pokebot-webhook:" + env.TELEGRAM_BOT_TOKEN);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 40);
}

const STATE_URL = (env) => `https://raw.githubusercontent.com/${env.GITHUB_REPO || DEFAULT_REPO}/bot-state/state.json`;

async function hmac(keyBytes, message) {
  const key = await crypto.subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(message)));
}

function hex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// Verifica la firma di Telegram sui dati della Mini App; restituisce l'utente o null.
async function verifyInitData(env, initData) {
  if (!initData) return null;
  const params = new URLSearchParams(initData);
  const hash = params.get("hash");
  if (!hash) return null;
  params.delete("hash");
  const dataCheck = [...params.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => `${k}=${v}`).join("\n");
  const secret = await hmac(new TextEncoder().encode("WebAppData"), env.TELEGRAM_BOT_TOKEN);
  const expected = hex(await hmac(secret, dataCheck));
  if (expected !== hash) return null;
  const authDate = Number(params.get("auth_date") || 0);
  if (!authDate || Date.now() / 1000 - authDate > 86400) return null; // dati più vecchi di un giorno
  try {
    return JSON.parse(params.get("user") || "null");
  } catch {
    return null;
  }
}

async function ownerChatId(env) {
  if (env.TELEGRAM_CHAT_ID) return String(env.TELEGRAM_CHAT_ID);
  try {
    const r = await fetch(STATE_URL(env) + "?t=" + Date.now(), { cf: { cacheTtl: 0 } });
    if (r.ok) {
      const st = await r.json();
      if (st.owner_chat_id) return String(st.owner_chat_id);
    }
  } catch {}
  return "";
}

async function telegram(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

async function dispatch(env, payload) {
  const repo = env.GITHUB_REPO || DEFAULT_REPO;
  return fetch(`https://api.github.com/repos/${repo}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pokebot-bridge",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });
}

function page(title, body) {
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>${title}</title><body style="font:17px system-ui;padding:24px;max-width:640px;margin:auto">
<h2>${title}</h2>${body}</body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
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
    // Mini App: stato (sempre fresco, aggira la cache del CDN di GitHub)
    if (url.pathname === "/api/state") {
      const r = await fetch(STATE_URL(env) + "?t=" + Date.now(), { cf: { cacheTtl: 0 } });
      return new Response(r.body, { status: r.status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });
    }
    // Mini App: comando (solo dal proprietario, firmato da Telegram)
    if (url.pathname === "/api/cmd" && request.method === "POST") {
      let body;
      try {
        body = await request.json();
      } catch {
        return Response.json({ ok: false, error: "richiesta non valida" }, { status: 400 });
      }
      const user = await verifyInitData(env, body.initData);
      if (!user) return Response.json({ ok: false, error: "non autenticato" }, { status: 401 });
      const owner = await ownerChatId(env);
      if (!owner || String(user.id) !== owner) return Response.json({ ok: false, error: "non sei il proprietario del bot" }, { status: 403 });
      const text = String(body.text || "").trim().slice(0, 4000);
      if (!text.startsWith("/")) return Response.json({ ok: false, error: "comando non valido" }, { status: 400 });
      const gh = await dispatch(env, { event_type: "telegram", client_payload: { chat_id: owner, text } });
      return Response.json({ ok: gh.status === 204, status: gh.status });
    }

    if (request.method === "GET") {
      if (!env.TELEGRAM_BOT_TOKEN || !env.GITHUB_TOKEN) {
        return page("⚠️ Mancano le variabili", "<p>Imposta <code>TELEGRAM_BOT_TOKEN</code> e <code>GITHUB_TOKEN</code> in Settings → Variables and Secrets, poi riapri questa pagina.</p>");
      }
      if (url.pathname === "/setup") {
        const res = await telegram(env, "setWebhook", {
          url: `${url.origin}/`,
          secret_token: await secretFor(env),
          allowed_updates: ["message", "callback_query"],
          drop_pending_updates: false,
        });
        // pulsante "App" accanto alla chat che apre la Mini App
        await telegram(env, "setChatMenuButton", {
          menu_button: { type: "web_app", text: "App", web_app: { url: `${url.origin}/app` } },
        });
        return page(res.ok ? "✅ Ponte attivo" : "❌ Errore",
          res.ok
            ? "<p>Da adesso ogni messaggio al bot avvia subito la ricerca. Torna su Telegram e scrivi <code>/stato</code>.</p>"
            : `<pre>${JSON.stringify(res, null, 2)}</pre>`);
      }
      if (url.pathname === "/reset") {
        const res = await telegram(env, "deleteWebhook", { drop_pending_updates: false });
        return page(res.ok ? "Ponte disattivato" : "Errore", `<pre>${JSON.stringify(res, null, 2)}</pre>`);
      }
      const info = await telegram(env, "getWebhookInfo");
      const active = info.ok && info.result && info.result.url === `${url.origin}/`;
      return page(active ? "✅ Ponte attivo" : "Ponte non attivo",
        `<p>${active ? "I messaggi al bot avviano subito il workflow." : "Apri <a href=\"/setup\">/setup</a> per attivarlo."}</p>
         <pre>${JSON.stringify(info.result || info, null, 2)}</pre>`);
    }

    if (request.method !== "POST") return new Response("ok");
    if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== (await secretFor(env))) {
      return new Response("forbidden", { status: 403 });
    }
    let update;
    try {
      update = await request.json();
    } catch {
      return new Response("bad request", { status: 400 });
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
    if (!msg || !text) return new Response("ok");
    const chatId = String(msg.chat.id);
    if (env.TELEGRAM_CHAT_ID && chatId !== String(env.TELEGRAM_CHAT_ID)) return new Response("ok");

    const gh = await dispatch(env, { event_type: "telegram", client_payload: { chat_id: chatId, text } });

    const ack = gh.status === 204
      ? "⏳ Ricevuto, avvio il bot: risposta tra circa un minuto."
      : `⚠️ GitHub ha risposto ${gh.status}: controlla GITHUB_TOKEN nel Worker.`;
    await telegram(env, "sendMessage", { chat_id: chatId, text: ack });
    return new Response("ok");
  },

  // Timer (vedi [triggers] in wrangler.toml): avvia la ricerca periodica su GitHub.
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env, { event_type: "timer", client_payload: { source: "cron", at: new Date(event.scheduledTime).toISOString() } }));
  },
};
