"""
Локальный прокси-адаптер для Telegram Bot API через Cloudflare Worker.

Зачем: api.telegram.org заблокирован в РФ.
Cloudflare Worker (telegram.d-megabyte.workers.dev) требует поле "text"
в каждом запросе, но getUpdates/getMe его не принимают.

Прокси:
1. Принимает POST от python-telegram-bot на localhost:PORT
2. Добавляет или удаляет поле "text" в зависимости от метода
3. Форвардит на https://telegram.d-megabyte.workers.dev/bot{TOKEN}/{method}
4. Возвращает ответ
"""

from __future__ import annotations

import json
import logging
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen, HTTPError
from urllib.parse import urlparse

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] proxy: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("tg-proxy")

def prepare_body_for_method(method: str, body_bytes: bytes) -> bytes:
    """
    Подготавливает тело запроса для Cloudflare Worker.

    Worker ТРЕБУЕТ поле 'text' в КАЖДОМ запросе.
    Telegram игнорирует лишние поля для методов, которые их не принимают.
    """
    if not body_bytes:
        body_bytes = b'{}'

    try:
        data = json.loads(body_bytes)
    except json.JSONDecodeError:
        return body_bytes

    if not isinstance(data, dict):
        return body_bytes

    # ВСЕГДА добавляем text (worker требует)
    data["text"] = "x"

    return json.dumps(data).encode()


class ProxyHandler(BaseHTTPRequestHandler):
    """Обработчик запросов HTTP."""

    # Подавляем логи BaseHTTPRequestHandler
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        """Прокси POST-запрос в Cloudflare Worker."""
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b'{}'

        # Определяем метод Telegram API из URL
        path = self.path
        # python-telegram-bot шлёт /bot{TOKEN}/method или /method
        match = re.match(r"/(?:bot[^/]+/)?(\w+)", path)
        if not match:
            self.send_error(400, f"Cannot parse method from path: {path}")
            return

        method = match.group(1)

        # Отправляем в Cloudflare Worker
        relay_url = self.server.relay_url  # type: ignore
        token = self.server.bot_token  # type: ignore
        target = f"{relay_url}/bot{token}/{method}"

        # Подготавливаем body для Worker (добавляем text)
        cleaned = prepare_body_for_method(method, body)

        req = Request(
            target,
            data=cleaned,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; BotProxy/1.0)",
            },
            method="POST",
        )

        try:
            with urlopen(req, timeout=25) as resp:
                response_data = resp.read()
                self.send_response(resp.status)
                # Прокидываем Content-Type
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(response_data)))
                self.end_headers()
                self.wfile.write(response_data)
        except HTTPError as e:
            # Прокидываем ошибку как есть
            error_body = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(error_body)))
            self.end_headers()
            self.wfile.write(error_body)
        except Exception as e:
            logger.error("Proxy error for %s: %s", method, e)
            self.send_error(502, f"Proxy error: {e}")

    def do_GET(self):
        """GET не поддерживаем — только POST."""
        self.send_error(405, "Only POST is supported")


class ProxyServer(HTTPServer):
    """HTTP-сервер с конфигурацией."""

    def __init__(self, addr, handler, relay_url, bot_token):
        self.relay_url = relay_url
        self.bot_token = bot_token
        super().__init__(addr, handler)


def main():
    """Точка входа."""
    relay_url = os.environ.get(
        "TELEGRAM_RELAY_URL",
        "https://telegram.d-megabyte.workers.dev",
    )
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    port = int(os.environ.get("PROXY_PORT", "8446"))

    if not bot_token:
        print("❌ TELEGRAM_BOT_TOKEN не задан")
        return

    server = ProxyServer(
        ("127.0.0.1", port),
        ProxyHandler,
        relay_url.rstrip("/"),
        bot_token,
    )

    print(f"✅ Telegram-прокси запущен на 127.0.0.1:{port}")
    print(f"   Реле: {relay_url}")
    print(f"   Для бота: base_url=http://127.0.0.1:{port}/bot")
    logger.info("Прокси запущен на порту %d", port)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nОстановка прокси...")
        server.shutdown()


if __name__ == "__main__":
    main()
