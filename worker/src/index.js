// Telegram Bot API Proxy Worker
// Поддерживает JSON и form-urlencoded body (PTB v20+ использует form)

const TG_API = "https://api.telegram.org/bot";

addEventListener("fetch", (event) => {
  event.respondWith(handle(event.request));
});

async function handle(request) {
  if (request.method !== "POST") {
    return json({ error: "POST only" }, 405);
  }

  const url = new URL(request.url);
  const match = url.pathname.match(/^\/bot([^/]+)\/(\w+)$/);
  if (!match) {
    return json({ error: "invalid path, expected /bot{TOKEN}/{method}" }, 400);
  }

  const token = match[1];
  const method = match[2];

  // Парсим body — поддерживаем JSON и form-urlencoded
  const contentType = request.headers.get("Content-Type") || "";
  let body;

  if (contentType.includes("application/x-www-form-urlencoded")) {
    const text = await request.text();
    body = Object.fromEntries(new URLSearchParams(text));
  } else {
    try {
      body = await request.json();
    } catch {
      body = {};
    }
  }

  const tgUrl = `${TG_API}${token}/${method}`;
  const tgResp = await fetch(tgUrl, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "User-Agent": "Mozilla/5.0 (compatible; TgProxyWorker/1.0)",
    },
    body: JSON.stringify(body),
  });

  const result = await tgResp.text();
  return new Response(result, {
    status: tgResp.status,
    headers: {
      "Content-Type": tgResp.headers.get("Content-Type") || "application/json",
    },
  });
}

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
