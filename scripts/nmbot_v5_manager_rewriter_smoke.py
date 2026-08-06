#!/usr/bin/env python3
"""One-case V5 manager-rewriter smoke harness.

Uses the existing gateway-agent -> OpenRouter route and does not modify the
configured prompt. The fixture keeps user history, assistant context, MCP
evidence, CTA policy, and the prepared answer in separate JSON sections.

Examples:
  python scripts/nmbot_v5_manager_rewriter_smoke.py --dry-run
  python scripts/nmbot_v5_manager_rewriter_smoke.py
  python scripts/nmbot_v5_manager_rewriter_smoke.py --output tmp/v5_smoke.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "tests" / "fixtures" / "v5_manager_rewriter_smoke.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def load_fixture(path: Path) -> tuple[dict[str, Any], str]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    prompt_path = path.parent / str(fixture["prompt_file"])
    prompt = prompt_path.read_text(encoding="utf-8")
    return fixture, prompt


def build_request(fixture: dict[str, Any], prompt: str, *, api_key: str) -> dict[str, Any]:
    payload = {key: value for key, value in fixture.items() if key not in {"prompt_file", "model", "temperature", "max_tokens", "timeout_seconds"}}
    return {
        "_payload_stage": "conversation_answer_manager_rewriter",
        "query": "V5_MANAGER_REWRITER_INPUT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True),
        "service": "openrouter",
        "model": str(fixture["model"]),
        "system_prompt": prompt,
        "parameters": {
            "temperature": float(fixture["temperature"]),
            "max_tokens": int(fixture["max_tokens"]),
        },
        "external_api_key": api_key,
    }


def safe_meta(meta: Any) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    allowed = {"model", "service", "response_time", "processing_time", "_gateway_task_id"}
    return {key: value for key, value in meta.items() if key in allowed}


async def run(fixture: dict[str, Any], prompt: str) -> dict[str, Any]:
    from scripts.chat_tester_bot import OvermindClient

    token = os.getenv("OVERMIND_TOKEN") or os.getenv("GATEWAY_POLL_TOKEN")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not token or not api_key:
        return {"status": "blocked", "reason": "missing_route_credentials"}

    client = OvermindClient()
    try:
        request = build_request(fixture, prompt, api_key=api_key)
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        text, meta = await client._run_gateway_request_once(
            request,
            headers,
            int(fixture["timeout_seconds"]),
        )
        failed = bool(meta.get("_safe_fallback")) if isinstance(meta, dict) else False
        return {
            "status": "failed" if failed or not str(text or "").strip() else "completed",
            "model": request["model"],
            "text": str(text or ""),
            "meta": safe_meta(meta),
        }
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    _load_dotenv(ROOT / ".env")
    fixture, prompt = load_fixture(args.fixture.resolve())
    if args.dry_run:
        request = build_request(fixture, prompt, api_key="<configured-openrouter-key>")
        print(json.dumps({
            "status": "dry_run",
            "payload_stage": request["_payload_stage"],
            "service": request["service"],
            "model": request["model"],
            "prompt_chars": len(prompt),
            "query_chars": len(request["query"]),
            "card_count": fixture["rewrite_policy"]["card_count"],
        }, ensure_ascii=False, indent=2))
        return 0

    result = asyncio.run(run(fixture, prompt))
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result.get("status") == "completed" else 2


if __name__ == "__main__":
    sys.exit(main())
