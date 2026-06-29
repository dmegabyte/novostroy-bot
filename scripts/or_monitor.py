#!/usr/bin/env python3
"""
Мониторинг + АВТОБЛОКИРОВКА расходов OpenRouter.

Лимиты: $5/час или $10/день.
При превышении — блокирует ключ во всех местах и шлёт уведомление в Telegram.

Cron: * * * * * (каждый час) - уже настроено.
"""

import json, os, sys, subprocess, shutil
from datetime import datetime
from urllib.request import Request, urlopen

LOG_FILE = "/tmp/or-monitor.log"
BACKUP_FILE = "/tmp/or-key-backup.txt"
VAULT_PATH = "secret/projects/NOVOSTROY_AI"
VAULT_FIELD = "openrouter_token"
DUMMY_KEY = "sk-or-v1-BLOCKED_BY_MONITOR_" + datetime.now().strftime("%Y%m%d_%H%M%S")

# ── Куда слать в TG ──────────────────────────────────────
TG_TOKEN = ""
TG_CHAT_ID = ""


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_key() -> str:
    """Достаём ключ OpenRouter"""
    key = os.getenv("OPENROUTER_API_KEY", "")
    if key and "BLOCKED" not in key:
        return key

    for p in [".env", "../.env", "/home/ser/projects/nmbot/.env",
              "/home/ser/projects/NOVOSTROY_AI/.env"]:
        if os.path.exists(p):
            for line in open(p):
                if line.startswith("OPENROUTER_API_KEY="):
                    v = line.strip().split("=", 1)[1].strip('"\'')
                    if v and "BLOCKED" not in v:
                        return v

    try:
        r = subprocess.run(
            ["vault", "kv", "get", "-format=json", VAULT_PATH],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            data = json.loads(r.stdout)
            v = data.get("data", {}).get("data", {}).get(VAULT_FIELD, "")
            if v and "BLOCKED" not in v:
                return v
    except Exception:
        pass
    return ""


def block_key_everywhere(real_key: str):
    """Блокируем ключ во всех местах"""
    log("🔒 БЛОКИРУЮ ключ OpenRouter...")

    # 1. Сохраняем реальный ключ на случай отката
    with open(BACKUP_FILE, "w") as f:
        f.write(real_key)
    log(f"   Ключ сохранён в {BACKUP_FILE}")

    # 2. Блокируем в .env файлах
    for p in [".env", "../.env", "/home/ser/projects/nmbot/.env",
              "/home/ser/projects/NOVOSTROY_AI/.env",
              "/home/ser/projects/NOVOSTROY_AI/bot/.env.bot"]:
        if os.path.exists(p):
            try:
                text = open(p).read()
                new_text = text.replace(real_key, DUMMY_KEY)
                if new_text != text:
                    open(p, "w").write(new_text)
                    log(f"   🔒 {p} — заблокирован")
            except Exception as e:
                log(f"   ❌ {p}: {e}")

    # 3. Блокируем в vault
    try:
        subprocess.run(
            ["vault", "kv", "put", VAULT_PATH, f"{VAULT_FIELD}={DUMMY_KEY}"],
            capture_output=True, timeout=5
        )
        log(f"   🔒 vault {VAULT_PATH}/{VAULT_FIELD} — заблокирован")
    except Exception as e:
        log(f"   ❌ vault: {e}")

    # 4. Меченый файл-флаг блокировки
    for d in ["/tmp", "/home/ser/projects/nmbot"]:
        try:
            open(f"{d}/OR_BLOCKED", "w").write(real_key)
        except:
            pass


def unblock_key():
    """Откат блокировки (из BACKUP_FILE)"""
    if os.path.exists(BACKUP_FILE):
        real_key = open(BACKUP_FILE).read().strip()
        log(f"🔓 Разблокировка ключом из {BACKUP_FILE}")
        # Восстанавливаем в .env
        for p in [".env", "../.env", "/home/ser/projects/nmbot/.env"]:
            if os.path.exists(p):
                text = open(p).read()
                if DUMMY_KEY in text:
                    open(p, "w").write(text.replace(DUMMY_KEY, real_key))
        # Восстанавливаем в vault
        try:
            subprocess.run(
                ["vault", "kv", "put", VAULT_PATH, f"{VAULT_FIELD}={real_key}"],
                capture_output=True, timeout=5
            )
        except:
            pass
        # Удаляем флаги
        for d in ["/tmp", "/home/ser/projects/nmbot"]:
            try:
                os.remove(f"{d}/OR_BLOCKED")
            except:
                pass
        os.remove(BACKUP_FILE)
        log("🔓 Разблокировано")


def send_tg(message: str):
    """Отправка в Telegram"""
    token = TG_TOKEN
    chat_id = TG_CHAT_ID

    # Достаём токен из .env если не указан
    if not token or not chat_id:
        for p in ["/home/ser/projects/nmbot/.env",
                  "/home/ser/projects/NOVOSTROY_AI/bot/.env.bot"]:
            if os.path.exists(p):
                for line in open(p):
                    if line.startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.strip().split("=", 1)[1].strip('"\'')
                    if line.startswith("TG_CHAT_ID=") or line.startswith("MY_TG_ID="):
                        chat_id = line.strip().split("=", 1)[1].strip('"\'')

    # Если chat_id не указан — шлём боту в личку (используем getUpdates)
    if not chat_id:
        try:
            r = urlopen(
                f"https://api.telegram.org/bot{token}/getUpdates?limit=1&timeout=5",
                timeout=5
            )
            updates = json.loads(r.read())
            for u in reversed(updates.get("result", [])):
                chat = u.get("message", {}).get("chat", {})
                if chat.get("type") == "private":
                    chat_id = str(chat.get("id"))
                    break
        except:
            pass

    if token and chat_id:
        import urllib.request
        data = json.dumps({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_notification": False,
        }).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"}
        )
        try:
            urllib.request.urlopen(req, timeout=10)
            log("   ✅ Уведомление отправлено в Telegram")
        except Exception as e:
            log(f"   ❌ Ошибка TG: {e}")


def main():
    key = get_key()
    if not key:
        log("❌ Ключ не найден или уже заблокирован")
        # Проверяем флаг блокировки
        for d in ["/tmp", "/home/ser/projects/nmbot"]:
            f = f"{d}/OR_BLOCKED"
            if os.path.exists(f):
                log("   ⚠️ Обнаружен флаг блокировки")
                send_tg("🚫 OpenRouter заблокирован. Ключ не активен.")
        sys.exit(1)

    # Запрашиваем статистику
    req = Request(
        "https://openrouter.ai/api/v1/auth/key",
        headers={"Authorization": f"Bearer {key}"},
    )
    try:
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read()).get("data", {})
    except Exception as e:
        log(f"❌ Ошибка OpenRouter API: {e}")
        return

    daily = data.get("usage_daily", 0)
    monthly = data.get("usage_monthly", 0)

    # ── Проверка лимитов ──
    LIMIT_DAY = 10  # $10 в день
    LIMIT_HOUR = 5   # $5 в час (грубая оценка)

    alerts = []
    block = False

    if daily > LIMIT_DAY:
        alerts.append(f"🔴 Дневной лимит ${LIMIT_DAY}: ${daily:.2f}")
        block = True
    if monthly > 50:
        alerts.append(f"🔴 Месячный лимит $50: ${monthly:.2f}")
        block = True

    if alerts:
        msg = (f"🚨 <b>OpenRouter ПРЕВЫШЕНИЕ ЛИМИТА!</b>\n"
               f"{chr(10).join(alerts)}\n"
               f"\n📊 Статистика:"
               f"\n  За сегодня: ${daily:.2f}"
               f"\n  За месяц: ${monthly:.2f}"
               f"\n\n🔒 Произвожу блокировку...")
        log(f"🚨 ПРЕВЫШЕНИЕ ЛИМИТА: {', '.join(alerts)}")
        send_tg(msg)

        if block:
            block_key_everywhere(key)
            send_tg("🔒 <b>OpenRouter ЗАБЛОКИРОВАН</b>\n"
                     f"Расход: ${daily:.2f}/день, ${monthly:.2f}/мес\n"
                     f"Ключ заменён на заглушку.\n"
                     f"Для разблокировки: or-unblock")
            log("🔒 ВСЕ ОБРАБОТКИ ОСТАНОВЛЕНЫ")
    else:
        log(f"✅ Лимиты в норме. День: ${daily:.2f}, Месяц: ${monthly:.2f}")


if __name__ == "__main__":
    main()
