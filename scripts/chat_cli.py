#!/usr/bin/env python3
"""
CLI-клиент для тестового чат-бота Novostroy AI (nmbot).

Использует gateway-agent + OpenRouter + MCP novostroym.

Запуск:
    cd projects/nmbot
    source .venv/bin/activate
    export $(grep -v '^#' .env | xargs)

    python scripts/chat_cli.py "Найди однушку до 8 млн в Москве"
    python scripts/chat_cli.py --search-model google/gemini-3.1-flash-lite-preview "Найди однушку"
    python scripts/chat_cli.py --chat-model google/gemini-2.5-flash "Найди однушку"
    python scripts/chat_cli.py --no-mcp "Кто ты?"
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

import scene_classifier
from style_scenes import get_scene_rules
import text_style_tool

# ── Конфигурация ─────────────────────────────────────────────

OVERMIND_URL = os.getenv("OVERMIND_URL", "https://overmind.aiaxel.ru")
OVERMIND_TOKEN = os.getenv("OVERMIND_TOKEN", "")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

# Experiment logging
ACTIVE_H_ID: str = os.getenv("NMBOT_H_ID", "H001")
LOGS_DIR: Path = Path(__file__).resolve().parent.parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

import logging  # noqa: E402
_LOGGER = logging.getLogger("nmbot_cli")
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _fh = logging.FileHandler(LOGS_DIR / "bot.log", encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _LOGGER.addHandler(_fh)


def _log_event(event: dict[str, Any]) -> None:
    """Пишет событие в logs/dialogs-YYYY-MM-DD.jsonl. Не падает при ошибке записи."""
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        event.setdefault("ts", datetime.now(timezone.utc).isoformat())
        event.setdefault("h_id", ACTIVE_H_ID)
        event.setdefault("source", "cli")
        path = LOGS_DIR / f"dialogs-{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[LOG-ERROR] {e}", file=sys.stderr)


def _strip_markdown(text: str) -> str:
    """H006: снимает markdown-обёртку ```json ... ``` (или ``` ... ```) вокруг JSON-блока.
    Если обёртки нет — возвращает текст как есть. Только для записи в лог."""
    t = text.strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl > 0:
            t = t[first_nl + 1 :]
        if t.endswith("```"):
            t = t[:-3].rstrip()
    return t


def _extract_response_text(text: str) -> str:
    raw = _strip_markdown(text).strip()
    try:
        s = raw.find("{")
        e = raw.rfind("}") + 1
        if s >= 0 and e > s:
            data = json.loads(raw[s:e])
            if isinstance(data, dict) and isinstance(data.get("response"), str):
                return data["response"]
    except json.JSONDecodeError:
        pass
    return raw


async def _maybe_style_text(
    session: aiohttp.ClientSession,
    text: str,
    *,
    context: str,
    intent: str,
    scene: str,
    scene_rules: str = "",
) -> str:
    if not STYLE_TOOL_ENABLED or not text.strip():
        return text
    try:
        styled, _meta = await text_style_tool.rewrite_text(
            session,
            text=text,
            context=context,
            intent=intent,
            tone="live",
            scene=scene,
            scene_rules=scene_rules,
            model=STYLE_TOOL_MODEL,
        )
        styled = (styled or "").strip()
        return styled or text
    except Exception as e:
        print(f"[WARN] text style tool failed: {e}", file=sys.stderr)
        return text

SEARCH_MODEL = "google/gemini-3.1-flash-lite-preview"
CHAT_MODEL = "google/gemini-2.5-flash"
STYLE_TOOL_ENABLED = os.getenv("NMBOT_TEXT_STYLE_TOOL", "1") != "0"
STYLE_TOOL_MODEL = os.getenv("NMBOT_STYLE_MODEL", "google/gemini-3.1-flash-lite-preview")

# Промпты читаются из prompts/*.txt (P002 — единый источник правды)
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    """Читает промпт из prompts/{name}.txt. Бросает FileNotFoundError с подсказкой."""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip()


SEARCH_SYSTEM_PROMPT = _load_prompt("search_v1.txt")
CHAT_SYSTEM_PROMPT = _load_prompt("chat_v1.txt")

# ── Overmind API ─────────────────────────────────────────────


async def create_task(
    session: aiohttp.ClientSession,
    request_data: dict,
    timeout_seconds: int = 300,
) -> dict:
    payload = {
        "agent_name": "gateway-agent",
        "endpoint": "/process",
        "request_data": request_data,
        "timeout_seconds": timeout_seconds,
        "max_retries": 0,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OVERMIND_TOKEN}",
    }
    url = f"{OVERMIND_URL.rstrip('/')}/api/v1/tasks/api"
    async with session.post(url, json=payload, headers=headers) as resp:
        result = await resp.json()
        if resp.status not in (200, 201):
            print(f"[ERROR] Создание задачи: HTTP {resp.status}", file=sys.stderr)
            print(json.dumps(result, ensure_ascii=False, indent=2), file=sys.stderr)
            return {}
        print(f"[OK] Задача создана: id={result.get('id')}", file=sys.stderr)
        return result


async def poll_task(session: aiohttp.ClientSession, task_id: int, timeout: int = 300) -> dict:
    headers = {"Authorization": f"Bearer {OVERMIND_TOKEN}"}
    base = OVERMIND_URL.rstrip("/")
    start = time.time()

    while time.time() - start < timeout:
        async with session.get(f"{base}/api/v1/tasks/api/{task_id}/status", headers=headers) as resp:
            status_data = await resp.json()

        status = status_data.get("status")
        if status in ("completed", "failed", "cancelled"):
            async with session.get(f"{base}/api/v1/tasks/api/{task_id}/result", headers=headers) as resp:
                return await resp.json()

        elapsed = int(time.time() - start)
        print(f"[WAIT] Задача {task_id}: статус={status} ({elapsed}с)", file=sys.stderr)
        await asyncio.sleep(3)

    raise TimeoutError(f"Задача {task_id} не завершилась за {timeout}с")


def print_result(result: dict) -> None:
    status = result.get("status", "unknown")
    result_obj = result.get("result") or result

    if isinstance(result_obj, dict):
        response_text = result_obj.get("response", "")
        error = result_obj.get("error", "")
        metadata = result_obj.get("metadata", {})

        print()
        print("─" * 60)
        print(f"Статус: {status}")
        if error:
            print(f"Ошибка: {error}")
        if response_text:
            print()
            print(response_text)
        if metadata:
            print()
            print("Метаданные:")
            print(json.dumps(metadata, ensure_ascii=False, indent=2))
        print("─" * 60)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))


async def ask_overmind(
    session: aiohttp.ClientSession,
    query: str,
    model: str,
    system_prompt: str,
    use_mcp: bool,
    timeout: int,
    max_tokens: int = 5000,
) -> tuple[str, dict]:
    request_data = {
        "query": query,
        "service": "openrouter",
        "model": model,
        "system_prompt": system_prompt,
        "parameters": {
            "temperature": 0.3,
            "max_tokens": max_tokens,
        },
        "external_api_key": OPENROUTER_API_KEY,
    }
    if use_mcp:
        request_data["mcp_servers"] = ["novostroym"]

    task = await create_task(session, request_data, timeout_seconds=timeout)
    task_id = task.get("id")
    if not task_id:
        return "", {}

    result = await poll_task(session, task_id, timeout=timeout)
    result_obj = result.get("result") or result
    if not isinstance(result_obj, dict):
        return json.dumps(result, ensure_ascii=False), result
    return result_obj.get("response", ""), result_obj


async def ask_two_stage(
    session: aiohttp.ClientSession,
    query: str,
    search_model: str,
    chat_model: str,
    use_mcp: bool,
    timeout: int,
    search_max_tokens: int = 5000,
    chat_max_tokens: int = 5000,
) -> tuple[str, str, dict, dict]:
    """Возвращает (search_text, chat_text, search_metadata, chat_metadata)."""
    search_response, search_meta = await ask_overmind(
        session=session,
        query=query,
        model=search_model,
        system_prompt=SEARCH_SYSTEM_PROMPT,
        use_mcp=use_mcp,
        timeout=timeout,
        max_tokens=search_max_tokens,
    )
    if not search_response:
        return "", "", search_meta, {}

    chat_query = (
        f"Запрос клиента: {query}\n\n"
        f"Найденные факты, которыми можно пользоваться:\n{search_response}"
    )
    chat_response, chat_meta = await ask_overmind(
        session=session,
        query=chat_query,
        model=chat_model,
        system_prompt=CHAT_SYSTEM_PROMPT,
        use_mcp=False,
        timeout=timeout,
        max_tokens=chat_max_tokens,
    )
    # H007-A: strip markdown-обёртку ДО парсинга/печати/логирования.
    chat_response = _extract_response_text(chat_response)
    scene_meta = await scene_classifier.classify_scene(
        session,
        user_text=query,
        search_response=search_response,
        memory={},
        draft_response=chat_response,
    )
    scene = str(scene_meta.get("scene") or "default_safe_reply")
    chat_response = await _maybe_style_text(
        session,
        chat_response,
        context=search_response,
        intent="chat_response",
        scene=scene,
        scene_rules=get_scene_rules(scene),
    )
    # H004: retry на пустой/мусорный chat-ответ (≤2 повтора)
    retries = 0
    while retries < 2 and (not chat_response or "{" not in chat_response):
        retries += 1
        _log_event({
            "kind": "user_message_retry",
            "stage": "chat",
            "attempt": retries,
            "raw_len": len(chat_response),
            "reason": "empty" if not chat_response else "no_json_brace",
        })
        chat_response, chat_meta = await ask_overmind(
            session=session,
            query=chat_query,
            model=chat_model,
            system_prompt=CHAT_SYSTEM_PROMPT,
            use_mcp=False,
            timeout=timeout,
            max_tokens=chat_max_tokens,
        )
        chat_response = _strip_markdown(chat_response)  # H007-A
    return search_response, chat_response, search_meta, chat_meta, retries


# ── Главная ──────────────────────────────────────────────────


async def main():
    # Проверка токенов
    if not OVERMIND_TOKEN:
        print("[ERROR] OVERMIND_TOKEN не задан", file=sys.stderr)
        print("  export OVERMIND_TOKEN='...'", file=sys.stderr)
        sys.exit(1)
    if not OPENROUTER_API_KEY:
        print("[ERROR] OPENROUTER_API_KEY не задан", file=sys.stderr)
        print("  export OPENROUTER_API_KEY='...'", file=sys.stderr)
        sys.exit(1)

    # Парсинг аргументов
    args = sys.argv[1:]
    search_model = SEARCH_MODEL
    chat_model = CHAT_MODEL
    use_mcp = True
    query_parts = []
    timeout = 300
    chat_max_tokens = 5000
    initial_params: dict = {}

    i = 0
    while i < len(args):
        if args[i] == "--search-model" and i + 1 < len(args):
            search_model = args[i + 1]
            i += 2
        elif args[i] == "--chat-model" and i + 1 < len(args):
            chat_model = args[i + 1]
            i += 2
        elif args[i] == "--model" and i + 1 < len(args):
            search_model = args[i + 1]
            chat_model = args[i + 1]
            i += 2
        elif args[i] == "--no-mcp":
            use_mcp = False
            i += 1
        elif args[i] == "--timeout" and i + 1 < len(args):
            timeout = int(args[i + 1])
            i += 2
        elif args[i] == "--chat-max-tokens" and i + 1 < len(args):
            chat_max_tokens = int(args[i + 1])
            i += 2
        elif args[i] == "--params" and i + 1 < len(args):
            import json as _json
            try:
                initial_params = _json.loads(args[i + 1])
            except _json.JSONDecodeError as _e:
                print(f"[ERROR] --params: невалидный JSON: {_e}", file=sys.stderr)
                sys.exit(1)
            i += 2
        elif args[i] == "--help" or args[i] == "-h":
            print(__doc__)
            sys.exit(0)
        else:
            query_parts.append(args[i])
            i += 1

    if not query_parts:
        print("[ERROR] Укажите запрос", file=sys.stderr)
        print("  python scripts/chat_cli.py 'Найди однушку в Москве'", file=sys.stderr)
        sys.exit(1)

    query = " ".join(query_parts)

    # Вывод информации
    print(f"Поиск:        {search_model}", file=sys.stderr)
    print(f"Общение:      {chat_model}", file=sys.stderr)
    print(f"MCP поиск:    {use_mcp}", file=sys.stderr)
    print(f"chat_max_tok: {chat_max_tokens}", file=sys.stderr)
    print(f"Таймаут:      {timeout}с", file=sys.stderr)
    print(f"Запрос:       {query}", file=sys.stderr)
    if initial_params:
        print(f"Начальные params: {json.dumps(initial_params, ensure_ascii=False)}", file=sys.stderr)
    print(file=sys.stderr)

    # H008: подмешиваем initial_params в query (как делает бот — chat_tester_bot.py:92,115-116)
    if initial_params:
        query = f"Текущие параметры: {json.dumps(initial_params, ensure_ascii=False)}\n\nКлиент: {query}"

    # Запуск
    t0 = time.monotonic()
    is_error = False
    error_msg = ""
    search_response = ""
    chat_response = ""
    search_meta: dict = {}
    chat_meta: dict = {}
    chat_retries: int = 0
    async with aiohttp.ClientSession() as session:
        try:
            search_response, chat_response, search_meta, chat_meta, chat_retries = await ask_two_stage(
                session=session,
                query=query,
                search_model=search_model,
                chat_model=chat_model,
                use_mcp=use_mcp,
                timeout=timeout,
                chat_max_tokens=chat_max_tokens,
            )
        except TimeoutError as e:
            is_error = True
            error_msg = str(e)
            print(f"[TIMEOUT] {e}", file=sys.stderr)

        duration_ms = int((time.monotonic() - t0) * 1000)

        # H007-B': Overmind отдаёт только tokens_used (одно число).
        # tokens_in/tokens_out/cost_usd недоступны — для биллинга использовать scripts/or_cost.py.
        def _extract(meta: dict) -> dict:
            if not isinstance(meta, dict):
                return {}
            md = meta["metadata"] if "metadata" in meta else meta
            if not isinstance(md, dict):
                return {}
            return {
                "tokens_used": md.get("tokens_used"),
            }

        search_cost = _extract(search_meta)
        chat_cost = _extract(chat_meta)
        total_tokens_used = sum(
            v for v in [search_cost.get("tokens_used"), chat_cost.get("tokens_used")] if isinstance(v, (int, float))
        )

        # Логируем в JSONL
        _log_event({
            "kind": "user_message",
            "user_text": query,
            "search_model": search_model,
            "chat_model": chat_model,
            "mcp": use_mcp,
            "search_response": search_response,
            "search_response_len": len(search_response) if search_response else 0,
            "response_text": _strip_markdown(chat_response),
            "response_len": len(chat_response) if chat_response else 0,
            "duration_ms": duration_ms,
            "chat_retries": chat_retries,
            "is_error": is_error,
            "error": error_msg,
            "cost": {
                "search": search_cost,
                "chat": chat_cost,
                "total_tokens_used": total_tokens_used or None,
            },
        })

        if is_error:
            sys.exit(1)

        print()
        print("─" * 60)
        print("Поисковые факты:")
        print(search_response)
        print()
        print("Ответ клиенту:")
        print(chat_response)
        print()
        # H007-B': печатаем tokens_used в stderr, cost_usd недоступен (см. or_cost.py)
        if total_tokens_used:
            print(f"📊 tokens_used={total_tokens_used} (cost: or_cost.py)", file=sys.stderr)
        print("─" * 60)


if __name__ == "__main__":
    asyncio.run(main())
