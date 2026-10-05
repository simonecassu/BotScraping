// Ponte Telegram → GitHub Actions (Cloudflare Worker, piano gratuito).
// Ogni messaggio inviato al bot avvia subito il workflow "PokéBot 30th" passando il comando.
//
// Variabili da impostare nel Worker (Settings → Variables and Secrets):
//   TELEGRAM_BOT_TOKEN  token di @BotFather
//   GITHUB_TOKEN        fine-grained token con permesso "Contents: Read and write" sul repo
//   GITHUB_REPO         es. simonecassu/BotScraping
//   WEBHOOK_SECRET      una parola a caso (la stessa usata in setWebhook)
//   TELEGRAM_CHAT_ID    (facoltativo) accetta solo questa chat
//
// Poi registra il webhook aprendo in Safari (una volta sola):
//   https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<nome-worker>.workers.dev/&secret_token=<WEBHOOK_SECRET>

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("PokéBot bridge ok");
    }
    if (env.WEBHOOK_SECRET && request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
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

    const gh = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO}/dispatches`, {
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
      : `⚠️ GitHub ha risposto ${gh.status}: controlla GITHUB_TOKEN e GITHUB_REPO nel Worker.`;
    await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: chatId, text: ack }),
    });
    return new Response("ok");
  },
};
