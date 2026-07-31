#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import shlex
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

LOG_DIR = Path(os.getenv("RESCUE_LOG_DIR", "/home/neiro/rescue-bot/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(LOG_DIR / "rescue-bot.log"),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
LOGGER = logging.getLogger("rescue-bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

TOKEN = os.getenv("RESCUE_BOT_TOKEN", "").strip()
TELEGRAM_API_BASE_URL = os.getenv("RESCUE_TELEGRAM_API_BASE_URL", "").strip().rstrip("/")
ALLOWED = {
    int(x.strip())
    for x in os.getenv("RESCUE_ALLOWED_USER_IDS", "").replace(";", ",").split(",")
    if x.strip().isdigit()
}
PUBLIC_IP = os.getenv("RESCUE_PUBLIC_IP", "193.107.155.236")
OPENCODE_API_URL = os.getenv("RESCUE_OPENCODE_API_URL", "http://127.0.0.1:4096").strip().rstrip("/")
OPENCODE_AGENT = os.getenv("RESCUE_OPENCODE_AGENT", "chati").strip()
OPENCODE_MODEL = os.getenv("RESCUE_OPENCODE_MODEL", "opencode/deepseek-v4-flash-free").strip()
OPENCODE_PUBLIC_URL = os.getenv("RESCUE_OPENCODE_PUBLIC_URL", f"http://{PUBLIC_IP}:4097")
OPENCODE_WEBCONSOLE_URL = os.getenv("RESCUE_OPENCODE_WEBCONSOLE_URL", f"http://{PUBLIC_IP}:8443")
OPENCODE_IPGATE_URL = os.getenv("RESCUE_OPENCODE_IPGATE_URL", f"http://{PUBLIC_IP}:8445")
WG_CONF = os.getenv("RESCUE_WG_CONF", "/home/neiro/wg0.conf")
MAX_REPLY = 3500


class AuthError(Exception):
    pass


def _uid(update: Update) -> int:
    return update.effective_user.id if update.effective_user else 0


def _check(update: Update) -> int:
    uid = _uid(update)
    if uid not in ALLOWED:
        LOGGER.warning(
            "DENY uid=%s command=%s",
            uid,
            update.effective_message.text if update.effective_message else "",
        )
        raise AuthError(f"Нет доступа для uid={uid}")
    return uid


def _clip(text: str) -> str:
    text = (text or "").strip()
    if len(text) > MAX_REPLY:
        return text[:MAX_REPLY] + "\n...<cut>"
    return text or "<empty>"


async def run_cmd(label: str, args: list[str], timeout: int = 20) -> tuple[int, str]:
    LOGGER.info("RUN %s: %s", label, " ".join(shlex.quote(a) for a in args))
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        text = out.decode("utf-8", "replace")
        LOGGER.info("DONE %s rc=%s out=%s", label, proc.returncode, text[-1000:])
        return proc.returncode, text
    except asyncio.TimeoutError:
        LOGGER.error("TIMEOUT %s", label)
        return 124, f"timeout after {timeout}s"
    except Exception as e:
        LOGGER.exception("ERROR %s", label)
        return 1, f"{type(e).__name__}: {e}"


async def post_json(label: str, url: str, payload: dict, timeout: int = 20) -> tuple[int, str]:
    LOGGER.info("POST %s: %s keys=%s", label, url, sorted(payload.keys()))
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            "/usr/bin/curl",
            "-sS",
            "--max-time",
            str(timeout),
            "-X",
            "POST",
            "-H",
            "Content-Type: application/json",
            "--data-binary",
            "@-",
            url,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(data), timeout=timeout + 2)
        text = out.decode("utf-8", "replace")
        LOGGER.info("POST_DONE %s rc=%s out=%s", label, proc.returncode, text[-1000:])
        return proc.returncode or 0, text
    except asyncio.TimeoutError:
        LOGGER.error("POST_TIMEOUT %s", label)
        return 124, f"timeout after {timeout}s"
    except Exception as e:
        LOGGER.exception("POST_ERROR %s", label)
        return 1, f"{type(e).__name__}: {e}"


async def http_json(
    label: str,
    method: str,
    url: str,
    payload: dict | None = None,
    timeout: int = 20,
) -> tuple[int, object, str]:
    LOGGER.info("HTTP %s %s: %s", method, label, url)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        args = [
            "/usr/bin/curl",
            "-sS",
            "--max-time",
            str(timeout),
            "-X",
            method,
        ]
        if payload is not None:
            args += [
                "-H",
                "Content-Type: application/json",
                "--data-binary",
                "@-",
            ]
        args.append(url)
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await asyncio.wait_for(proc.communicate(data), timeout=timeout + 2)
        text = out.decode("utf-8", "replace")
        LOGGER.info("HTTP_DONE %s rc=%s out=%s", label, proc.returncode, text[-1000:])
        parsed: object
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = text
        return proc.returncode or 0, parsed, text
    except asyncio.TimeoutError:
        LOGGER.error("HTTP_TIMEOUT %s", label)
        return 124, f"timeout after {timeout}s", f"timeout after {timeout}s"
    except Exception as e:
        LOGGER.exception("HTTP_ERROR %s", label)
        msg = f"{type(e).__name__}: {e}"
        return 1, msg, msg


def _extract_opencode_text(data: object) -> str:
    if not isinstance(data, dict):
        return str(data)
    if data.get("response_text"):
        return str(data["response_text"]).strip()
    parts = data.get("parts") or []
    if isinstance(parts, list):
        text_parts = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                text_parts.append(str(part["text"]))
        if text_parts:
            return "\n\n".join(text_parts).strip()
    for key in ("content", "text", "message"):
        value = data.get(key)
        if value:
            return str(value).strip()
    return json.dumps(data, ensure_ascii=False, indent=2)


def _opencode_model_payload(model: str) -> object | None:
    model = (model or "").strip()
    if not model:
        return None
    if "/" not in model:
        return {"modelID": model}
    provider_id, model_id = model.split("/", 1)
    provider_id = provider_id.strip()
    model_id = model_id.strip()
    if not provider_id or not model_id:
        return {"modelID": model}
    return {"providerID": provider_id, "modelID": model_id}


async def reply(update: Update, title: str, body: str) -> None:
    await update.effective_message.reply_text(
        f"{title}\n```\n{_clip(body)}\n```",
        parse_mode="Markdown",
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        uid = _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(
        "Rescue bot online. Доступ разрешён для uid=%s.\n"
        "Команды: /status, /opencode, /oc, /wg_down, /fix_route, /restart_opencode, /help" % uid
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    await update.effective_message.reply_text(
        "/status — состояние VPS, маршрутов, сервисов\n"
        "/opencode — ссылки и статус OpenCode-доступа\n"
        "/oc текст — отправить текст в OpenCode и получить ответ\n"
        "/wg_down — sudo wg-quick down /home/neiro/wg0.conf\n"
        "/fix_route — sudo ip rule add pref 100 from PUBLIC_IP lookup main\n"
        "/restart_opencode — restart opencode.service и opencode-proxy.service\n"
        "Произвольный shell не поддерживается."
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    cmds = [
        ["bash", "-lc", "date -u; hostname; uptime -p"],
        ["/usr/sbin/ip", "-br", "addr", "show", "wg0"],
        ["/usr/sbin/ip", "rule", "show"],
        ["/usr/sbin/ip", "route", "get", "1.1.1.1"],
        [
            "/usr/bin/systemctl",
            "--user",
            "is-active",
            "novostroy-bot.service",
            "opencode.service",
            "opencode-proxy.service",
        ],
        ["bash", "-lc", "ss -lntp 2>/dev/null | egrep ':(1905|4096|4097|8443|8444|8445)' || true"],
    ]
    parts = []
    for c in cmds:
        rc, out = await run_cmd("status", c, timeout=10)
        parts.append("$ " + " ".join(shlex.quote(x) for x in c) + f"\nrc={rc}\n{out}")
    await reply(update, "STATUS", "\n---\n".join(parts))


async def opencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    header = (
        "OpenCode links:\n"
        f"- local api: {OPENCODE_API_URL}\n"
        f"- agent: {OPENCODE_AGENT or '-'}\n"
        f"- model: {OPENCODE_MODEL or '-'}\n"
        f"- proxy/API: {OPENCODE_PUBLIC_URL}\n"
        f"- webconsole: {OPENCODE_WEBCONSOLE_URL}\n"
        f"- ipgate/mobile: {OPENCODE_IPGATE_URL}\n\n"
        "Если не открывается после WireGuard — сначала /fix_route, потом /restart_opencode.\n"
        "Важно: доступ ограничивается whitelist на сервере, не открывай порт всем."
    )
    cmds = [
        [
            "/usr/bin/systemctl",
            "--user",
            "is-active",
            "opencode.service",
            "opencode-proxy.service",
        ],
        ["bash", "-lc", "ss -lntp 2>/dev/null | egrep ':(4096|4097|8443|8444|8445)' || true"],
        ["/usr/sbin/ip", "rule", "show"],
    ]
    parts = [header]
    for c in cmds:
        rc, out = await run_cmd("opencode", c, timeout=10)
        parts.append("$ " + " ".join(shlex.quote(x) for x in c) + f"\nrc={rc}\n{out}")
    await reply(update, "OPENCODE", "\n---\n".join(parts))


async def oc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text(
            "Напиши так:\n/oc проверь статус проекта\n\n"
            "Я добавлю текст в OpenCode prompt. Произвольный shell не выполняется."
        )
        return
    if len(text) > 3000:
        await update.effective_message.reply_text("Слишком длинно. Лимит для /oc — 3000 символов.")
        return
    session_title = f"tg: {text[:40].replace(chr(10), ' ')}"
    rc, created, raw = await http_json(
        "opencode_create_session",
        "POST",
        f"{OPENCODE_API_URL}/session",
        {"title": session_title},
        timeout=15,
    )
    if rc != 0 or not isinstance(created, dict):
        fallback_rc, fallback_out = await post_json(
            "opencode_append_prompt",
            f"{OPENCODE_API_URL}/tui/append-prompt",
            {"text": text},
            timeout=15,
        )
        if fallback_rc == 0 and fallback_out.strip() == "true":
            await update.effective_message.reply_text("Готово, отправил текст в OpenCode prompt.")
            return
        await reply(update, f"OPENCODE_SEND rc={rc}", raw)
        return

    session_id = str(created.get("id") or created.get("session_id") or "").strip()
    if not session_id:
        await reply(update, "OPENCODE_SESSION_ERROR", raw)
        return

    msg_rc, msg_data, msg_raw = await http_json(
        "opencode_send_message",
        "POST",
        f"{OPENCODE_API_URL}/session/{session_id}/message",
        {
            "parts": [{"type": "text", "text": text}],
            **({"agent": OPENCODE_AGENT} if OPENCODE_AGENT else {}),
            **({"model": _opencode_model_payload(OPENCODE_MODEL)} if OPENCODE_MODEL else {}),
        },
        timeout=300,
    )
    response_text = _extract_opencode_text(msg_data)
    if msg_rc != 0:
        await reply(update, f"OPENCODE_MESSAGE rc={msg_rc} session={session_id}", msg_raw)
    else:
        await update.effective_message.reply_text(
            f"OpenCode session={session_id} agent={OPENCODE_AGENT or '-'} model={OPENCODE_MODEL or '-'}\n{_clip(response_text)}"
        )

    cleanup_rc = 0
    cleanup_out = "skipped"
    if msg_rc == 0:
        cleanup_rc, cleanup_data, cleanup_out = await http_json(
            "opencode_delete_session",
            "DELETE",
            f"{OPENCODE_API_URL}/session/{session_id}",
            timeout=10,
        )
        LOGGER.info("OPENCODE_CLEANUP session=%s rc=%s out=%s data=%s", session_id, cleanup_rc, cleanup_out, cleanup_data)


async def wg_down(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    rc, out = await run_cmd(
        "wg_down",
        ["/usr/bin/sudo", "-n", "/usr/bin/wg-quick", "down", WG_CONF],
        timeout=40,
    )
    suffix = "\n\nЕсли rc=1 и там sudo password required — поставь sudoers whitelist." if rc else ""
    await reply(update, f"WG_DOWN rc={rc}", out + suffix)


async def fix_route(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    rc, out = await run_cmd(
        "fix_route",
        [
            "/usr/bin/sudo",
            "-n",
            "/usr/sbin/ip",
            "rule",
            "add",
            "pref",
            "100",
            "from",
            f"{PUBLIC_IP}/32",
            "lookup",
            "main",
        ],
        timeout=20,
    )
    suffix = "\n\nЕсли правило уже есть, это нормально." if rc else ""
    await reply(update, f"FIX_ROUTE rc={rc}", out + suffix)


async def restart_opencode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        _check(update)
    except AuthError as e:
        await update.effective_message.reply_text(str(e))
        return
    rc, out = await run_cmd(
        "restart_opencode",
        ["/usr/bin/systemctl", "--user", "restart", "opencode.service", "opencode-proxy.service"],
        timeout=40,
    )
    rc2, out2 = await run_cmd(
        "opencode_status",
        ["/usr/bin/systemctl", "--user", "is-active", "opencode.service", "opencode-proxy.service"],
        timeout=10,
    )
    await reply(update, f"RESTART_OPENCODE rc={rc}", out + "\n--- status ---\n" + out2 + f"\nstatus_rc={rc2}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("telegram error: %s", context.error)


def main() -> None:
    if not TOKEN:
        raise SystemExit("RESCUE_BOT_TOKEN is not set")
    if not ALLOWED:
        raise SystemExit("RESCUE_ALLOWED_USER_IDS is not set")
    builder = Application.builder().token(TOKEN)
    if TELEGRAM_API_BASE_URL:
        builder = builder.base_url(TELEGRAM_API_BASE_URL)
    app = builder.build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("opencode", opencode))
    app.add_handler(CommandHandler("oc", oc))
    app.add_handler(CommandHandler("wg_down", wg_down))
    app.add_handler(CommandHandler("fix_route", fix_route))
    app.add_handler(CommandHandler("restart_opencode", restart_opencode))
    app.add_error_handler(error_handler)
    LOGGER.info("rescue-bot starting allowed=%s public_ip=%s wg_conf=%s", sorted(ALLOWED), PUBLIC_IP, WG_CONF)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
