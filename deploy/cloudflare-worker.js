// Ponte Telegram → GitHub Actions (Cloudflare Worker, piano gratuito).
// Ogni messaggio inviato al bot avvia subito il workflow "PokéBot 30th" passando il comando.
//
// Variabili da impostare nel Worker (Settings → Variables and Secrets, tipo "Secret"):
//   TELEGRAM_BOT_TOKEN  token di @BotFather
//   GITHUB_TOKEN        fine-grained token con permesso "Contents: Read and write" sul repository
//   GITHUB_REPO         (facoltativo) default simonecassu/BotScraping
//   TELEGRAM_CHAT_ID    (facoltativo) accetta solo questa chat
//
// Dopo il deploy apri UNA volta in Safari:  https://<indirizzo-del-worker>/setup
// Il worker registra da solo il webhook su Telegram. Apri /status per controllare, /reset per tornare alla modalità base.

const DEFAULT_REPO = "simonecassu/BotScraping";

async function secretFor(env) {
  // segreto del webhook derivato dal token: niente da configurare a mano
  const data = new TextEncoder().encode("pokebot-webhook:" + env.TELEGRAM_BOT_TOKEN);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return [...new Uint8Array(hash)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 40);
}

async function telegram(env, method, body) {
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
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

    if (request.method === "GET") {
      if (!env.TELEGRAM_BOT_TOKEN || !env.GITHUB_TOKEN) {
        return page("⚠️ Mancano le variabili", "<p>Imposta <code>TELEGRAM_BOT_TOKEN</code> e <code>GITHUB_TOKEN</code> in Settings → Variables and Secrets, poi riapri questa pagina.</p>");
      }
      if (url.pathname === "/setup") {
        const res = await telegram(env, "setWebhook", {
          url: `${url.origin}/`,
          secret_token: await secretFor(env),
          allowed_updates: ["message"],
          drop_pending_updates: false,
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
    const msg = update.message;
    if (!msg || !msg.text) return new Response("ok");
    const chatId = String(msg.chat.id);
    if (env.TELEGRAM_CHAT_ID && chatId !== String(env.TELEGRAM_CHAT_ID)) return new Response("ok");

    const repo = env.GITHUB_REPO || DEFAULT_REPO;
    const gh = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pokebot-bridge",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ event_type: "telegram", client_payload: { chat_id: chatId, text: msg.text } }),
    });

    const ack = gh.status === 204
      ? "⏳ Ricevuto, avvio il bot: risposta tra circa un minuto."
      : `⚠️ GitHub ha risposto ${gh.status}: controlla GITHUB_TOKEN nel Worker.`;
    await telegram(env, "sendMessage", { chat_id: chatId, text: ack });
    return new Response("ok");
  },
};
