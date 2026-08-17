#!/usr/bin/env python3
"""Run one explicitly bounded V6 test layer."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
PROMPTFOO = ("promptfoo", "eval")
RUNTIME_TESTS = (
    "tests/test_nmbot_v6_simple_contract.py",
    "tests/test_nmbot_v6_simple_runtime.py",
    "tests/test_nmbot_v6_simple_phone_outbox.py",
    "tests/test_nmbot_v6_url_card_branch.py",
    "tests/test_nmbot_v6_finance_prompt.py",
)
LAYERS = {
    "prompt1": {
        "provider": True, "network": True, "vps_jivo": False,
        "proves": "Модельное семантическое решение Prompt 1 на синтетическом входе без MCP-вызова.",
        "does_not_prove": "Не доказывает MCP, runtime, релиз или поведение Jivo.",
    },
    "prompt2": {
        "provider": True, "network": True, "vps_jivo": False,
        "proves": "Модельный answer/final_question Prompt 2 на синтетическом материале.",
        "does_not_prove": "Не доказывает state/router, MCP, релиз или поведение Jivo.",
    },
    "runtime": {
        "provider": False, "network": False, "vps_jivo": False,
        "proves": "Офлайн контракты и runtime с fake/stub model ports.",
        "does_not_prove": "Не доказывает ответ реального провайдера, MCP или Jivo.",
    },
    "contour": {
        "provider": True, "network": True, "vps_jivo": True,
        "proves": "TEST-only Jivo smoke: bridge и опубликованный V6 результат в целевом контуре.",
        "does_not_prove": "Не заменяет полный релизный набор проверок и не следует из нижних уровней.",
    },
}


def _dotenv_key(path: Path, key: str) -> str | None:
    """Read one dotenv value without logging it."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        candidate, value = line.split("=", 1)
        if candidate.strip().removeprefix("export ").strip() == key:
            return value.strip().strip('"').strip("'") or None
    return None


def build_command(layer: str) -> list[str]:
    if layer == "runtime":
        return [sys.executable, "-m", "pytest", "-q", *RUNTIME_TESTS]
    if layer in {"prompt1", "prompt2"}:
        config = "prompt1.yaml" if layer == "prompt1" else "prompt2-contextual.yaml"
        return [*PROMPTFOO, "-c", f"eval/nmbot-v6-layered/{config}", "--no-cache", "--max-concurrency", "1"]
    return [sys.executable, "scripts/nmbot_v6_jivo_smoke.py", "--activate-v6", "--require-accepted"]


def _proof_boundary(layer: str) -> str:
    item = LAYERS[layer]
    return f"proves: {item['proves']} Does not prove: {item['does_not_prove']}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="print layer inventory as JSON")
    parser.add_argument("--layer", choices=tuple(LAYERS))
    parser.add_argument("--execute", action="store_true", help="run instead of showing the exact command")
    parser.add_argument("--confirm-model", action="store_true", help="confirm provider/token use for a prompt layer")
    parser.add_argument("--confirm-live", action="store_true", help="confirm TEST-only Jivo/VPS smoke")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env", help="dotenv containing OPENROUTER_API_KEY")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    args = _parser().parse_args(argv)
    if args.list:
        if args.layer:
            _parser().error("--list cannot be combined with --layer")
        print(json.dumps({"layers": LAYERS}, ensure_ascii=False, sort_keys=True))
        return 0
    if not args.layer:
        _parser().error("--layer is required unless --list is used")

    command = build_command(args.layer)
    if not args.execute:
        print(json.dumps({"dry_run": True, "layer": args.layer, "command": command,
                          "proof_boundary": _proof_boundary(args.layer)}, ensure_ascii=False, sort_keys=True))
        return 0
    if args.layer in {"prompt1", "prompt2"} and not args.confirm_model:
        print("Refusing model execution: add --confirm-model with --execute.", file=sys.stderr)
        return 2
    if args.layer == "contour" and not args.confirm_live:
        print("Refusing live contour execution: add --confirm-live with --execute.", file=sys.stderr)
        return 2

    child_env = dict(os.environ if environ is None else environ)
    if args.layer in {"prompt1", "prompt2"} and not child_env.get("OPENROUTER_API_KEY"):
        key = _dotenv_key(args.env_file, "OPENROUTER_API_KEY")
        if not key:
            print("OPENROUTER_API_KEY is missing; promptfoo was not invoked.", file=sys.stderr)
            return 2
        child_env["OPENROUTER_API_KEY"] = key
    completed = run(command, cwd=ROOT, env=child_env, shell=False, check=False, text=True)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
