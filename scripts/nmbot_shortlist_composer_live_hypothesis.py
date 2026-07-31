#!/usr/bin/env python3
from __future__ import annotations

"""One-call read-only model probe for the isolated shortlist composer hypothesis."""

import argparse
import asyncio
import copy
import importlib.util
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
MATRIX_PATH = REGISTRY / "shortlist_composer_matrix.json"
DEFAULT_CASE = "shortlist_sparse_family_three_options"
DEFAULT_MODEL = "google/gemini-2.5-flash"
Gateway = Callable[[dict[str, Any], int], Awaitable[tuple[str, Mapping[str, Any]]]]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"{name} loader is missing")
    spec.loader.exec_module(module)
    return module


composer = _load_module("shortlist_composer_hypothesis", REGISTRY / "shortlist_composer_hypothesis.py")


def load_case(case_id: str) -> dict[str, Any]:
    cases = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    for case in cases:
        if case.get("case_id") == case_id and case.get("expected_valid") is True:
            return case
    raise ValueError("unknown or non-positive hypothesis case")


def build_request(case: Mapping[str, Any], *, temperature: float, model: str, repair_errors: tuple[str, ...] = ()) -> dict[str, Any]:
    package = composer.build_model_input(case["input"])
    payload: dict[str, Any] = {"input": package["input"]}
    if repair_errors:
        payload["repair_validation_errors"] = list(repair_errors)
        payload["repair_instructions"] = _repair_instructions(repair_errors)
    return {
        "_payload_stage": "conversation_answer",
        "query": "SHORTLIST_COMPOSER_INPUT=" + json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\nВерни только строгий JSON ответа.",
        "service": "openrouter",
        "model": model,
        "system_prompt": package["system_prompt"],
        "parameters": {"temperature": temperature, "max_tokens": 1800},
    }


def _repair_instructions(errors: tuple[str, ...]) -> list[str]:
    instructions = []
    for error in errors:
        if error == "bureaucratic_style":
            instructions.append("Перепиши канцелярские обороты живым русским языком, сохранив факты, роли и точный CTA.")
        elif error == "option_name_repeated":
            instructions.append("Убери собственное название ЖК из presentation: оно уже будет в заголовке.")
        elif error == "duplicate_presentation":
            instructions.append("Дай карточкам разные подтвержденные акценты согласно decision_role.")
        elif error == "recommendation_cta_repetition":
            instructions.append("Сделай recommendation независимым коротким выводом; если нового вывода нет, верни пустую строку и не повторяй финальный вопрос.")
        elif error == "common_fact_repeated":
            instructions.append("Оставь общие факты только во вступлении и убери их из presentation.")
        elif error in {"unsupported_claim", "unknown_number", "invented_comparative_number", "ungrounded_field"}:
            instructions.append("Используй только буквальные поля и точные сравнения из входа; не добавляй новые факты или числа.")
        elif error in {"unavailable_field_claim", "undeclared_field_claim"}:
            instructions.append("Убери признаки, которых нет в fields текущей карточки; во вступлении оставь только shared_field_ids.")
        elif error == "scenario_field_coverage_missing":
            instructions.append("Верни в каждую presentation все переданные обязательные поля текущего сценария и добавь их field_id в used_field_ids.")
        elif error in {"investment_counter_inference", "investment_counter_caveat_missing"}:
            instructions.append("Оставь sales_count/ads_count буквальными, убери оправдание цены; одну нейтральную caveat «без рыночного или финансового прогноза и без вывода о будущем результате» помести во вступление, не повторяй в карточках.")
        elif error == "internal_leak":
            instructions.append("Убери слова «данный», «данные», «карточка», «подтверждено» и внутренние термины; пиши живо: «по известным фактам явного преимущества нет».")
    return list(dict.fromkeys(instructions))[:5]


def parse_candidate(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
    text = str(raw or "").strip()
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None, ["invalid_json"]
    if not isinstance(value, dict):
        return None, ["json_root_not_object"]
    return value, []


def _drop_redundant_recommendation(case: Mapping[str, Any], candidate: dict[str, Any] | None, result: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any], bool]:
    if candidate is None or set(result.get("errors", [])) != {"recommendation_cta_repetition"}:
        return candidate, result, False
    cleaned = copy.deepcopy(candidate)
    cleaned["recommendation"] = ""
    cleaned_result = composer.simulate(case["input"], cleaned)
    if not cleaned_result["valid"]:
        return candidate, result, False
    return cleaned, cleaned_result, True


def _sanitize_internal_wording(case: Mapping[str, Any], candidate: dict[str, Any] | None, result: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any], bool]:
    if candidate is None or set(result.get("errors", [])) != {"internal_leak"}:
        return candidate, result, False
    cleaned = copy.deepcopy(candidate)

    def clean(text: str) -> str:
        text = re.sub(r"\bподтвержд[её]нн\w*\s+преимуществ\w*\b", "явного преимущества", text, flags=re.IGNORECASE)
        text = re.sub(r"\bданн(?:ый|ая|ое|ые)\s+жк\b", "этот ЖК", text, flags=re.IGNORECASE)
        text = re.sub(r"\bпо\s+(?:данн\w*|карточк\w*)\b", "по известным фактам", text, flags=re.IGNORECASE)
        text = re.sub(r"\bбуквальн\w*\s+данн\w*\b", "буквальными числами", text, flags=re.IGNORECASE)
        return text

    cleaned["intro"] = clean(str(cleaned.get("intro", "")))
    cleaned["recommendation"] = clean(str(cleaned.get("recommendation", "")))
    for option in cleaned.get("options", []):
        if isinstance(option, dict):
            option["presentation"] = clean(str(option.get("presentation", "")))
    if cleaned == candidate:
        return candidate, result, False
    cleaned_result = composer.simulate(case["input"], cleaned)
    if not cleaned_result["valid"]:
        return candidate, result, False
    return cleaned, cleaned_result, True


def _strip_repeated_option_name_prefix(case: Mapping[str, Any], candidate: dict[str, Any] | None, result: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any], bool]:
    if candidate is None or "option_name_repeated" not in set(result.get("errors", [])):
        return candidate, result, False
    cleaned = copy.deepcopy(candidate)
    changed = False
    for option in cleaned.get("options", []):
        if not isinstance(option, dict):
            continue
        name = str(option.get("object_name", "")).strip()
        presentation = str(option.get("presentation", "")).strip()
        if name and presentation.casefold().startswith(name.casefold()):
            presentation = presentation[len(name):].lstrip(" —:,.\t")
            if presentation:
                presentation = presentation[0].upper() + presentation[1:]
            option["presentation"] = presentation
            changed = True
    if not changed:
        return candidate, result, False
    cleaned_result = composer.simulate(case["input"], cleaned)
    return cleaned, cleaned_result, True


def _sanitize_known_bureaucratic_wording(case: Mapping[str, Any], candidate: dict[str, Any] | None, result: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any], bool]:
    if candidate is None or set(result.get("errors", [])) != {"bureaucratic_style"}:
        return candidate, result, False
    cleaned = copy.deepcopy(candidate)

    def clean(text: str) -> str:
        return re.sub(
            r"\bэтот\s+вариант\s+(?:может\s+быть|будет)\s+предпочтительн\w*\b",
            "этот вариант стоит рассмотреть",
            text,
            flags=re.IGNORECASE,
        )

    cleaned["intro"] = clean(str(cleaned.get("intro", "")))
    cleaned["recommendation"] = clean(str(cleaned.get("recommendation", "")))
    for option in cleaned.get("options", []):
        if isinstance(option, dict):
            option["presentation"] = clean(str(option.get("presentation", "")))
    if cleaned == candidate:
        return candidate, result, False
    cleaned_result = composer.simulate(case["input"], cleaned)
    if not cleaned_result["valid"]:
        return candidate, result, False
    return cleaned, cleaned_result, True


def _drop_investment_counter_recommendation(case: Mapping[str, Any], candidate: dict[str, Any] | None, result: Mapping[str, Any]) -> tuple[dict[str, Any] | None, Mapping[str, Any], bool]:
    if case.get("scenario") != "investment" or candidate is None or "investment_counter_inference" not in set(result.get("errors", [])):
        return candidate, result, False
    if not str(candidate.get("recommendation", "")).strip():
        return candidate, result, False
    cleaned = copy.deepcopy(candidate)
    cleaned["recommendation"] = ""
    return cleaned, composer.simulate(case["input"], cleaned), True


async def run_live_case(
    case_id: str = DEFAULT_CASE,
    *,
    temperature: float,
    model: str = DEFAULT_MODEL,
    timeout: int = 90,
    gateway_func: Gateway | None = None,
) -> dict[str, Any]:
    case = load_case(case_id)
    request = build_request(case, temperature=temperature, model=model)
    if gateway_func is None:
        scripts = str(ROOT / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import nmbot_v2_search_mcp_probe as gateway_probe

        gateway_probe.load_env()
        gateway_func = gateway_probe.gateway_request
    started = time.monotonic()
    try:
        raw, meta = await gateway_func(request, timeout)
    except Exception as exc:  # noqa: BLE001 - report only the exception class.
        return {
            "case": case_id,
            "ok": False,
            "network": True,
            "model": model,
            "temperature": temperature,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "errors": ["gateway_exception", type(exc).__name__],
            "candidate": None,
            "text": "",
            "manual_review_required": True,
            "metadata": {},
            "gateway_meta": {"ok": False},
        }
    if not isinstance(meta, Mapping) or meta.get("ok") is not True:
        return {
            "case": case_id,
            "ok": False,
            "network": True,
            "model": model,
            "temperature": temperature,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "errors": ["gateway_not_ok"],
            "candidate": None,
            "text": "",
            "manual_review_required": True,
            "metadata": {},
            "gateway_meta": {"ok": False},
        }
    candidate, parse_errors = parse_candidate(raw)
    if candidate is None:
        result = {"valid": False, "errors": parse_errors, "text": "", "manual_review_required": True, "metadata": {}}
    else:
        result = composer.simulate(case["input"], candidate)
    initial_errors = list(result["errors"])
    attempts = 1
    status = "primary" if result["valid"] else "failed"
    postprocess = []
    candidate, result, recommendation_dropped = _drop_redundant_recommendation(case, candidate, result)
    if recommendation_dropped:
        status = "sanitized"
        postprocess.append("blank_redundant_recommendation")
    candidate, result, name_stripped = _strip_repeated_option_name_prefix(case, candidate, result)
    if name_stripped:
        status = "sanitized" if result["valid"] else status
        postprocess.append("strip_repeated_option_name_prefix")
    candidate, result, investment_recommendation_dropped = _drop_investment_counter_recommendation(case, candidate, result)
    if investment_recommendation_dropped:
        status = "sanitized" if result["valid"] else status
        if "blank_investment_counter_recommendation" not in postprocess:
            postprocess.append("blank_investment_counter_recommendation")
    candidate, result, wording_sanitized = _sanitize_internal_wording(case, candidate, result)
    if wording_sanitized:
        status = "sanitized"
        postprocess.append("sanitize_internal_wording")
    candidate, result, bureaucracy_sanitized = _sanitize_known_bureaucratic_wording(case, candidate, result)
    if bureaucracy_sanitized:
        status = "sanitized"
        postprocess.append("sanitize_known_bureaucratic_wording")
    if candidate is not None and not result["valid"] and initial_errors:
        attempts = 2
        repair_request = build_request(case, temperature=temperature, model=model, repair_errors=tuple(initial_errors[:6]))
        try:
            repair_raw, repair_meta = await gateway_func(repair_request, timeout)
        except Exception as exc:  # noqa: BLE001 - safe class only.
            result = {"valid": False, "errors": initial_errors + ["repair_gateway_exception", type(exc).__name__], "text": "", "manual_review_required": True, "metadata": {}}
        else:
            repaired_candidate, repair_parse_errors = parse_candidate(repair_raw)
            if isinstance(repair_meta, Mapping) and repair_meta.get("ok") is True and repaired_candidate is not None:
                repaired_result = composer.simulate(case["input"], repaired_candidate)
                candidate = repaired_candidate
                result = repaired_result
                status = "repaired" if repaired_result["valid"] else "failed"
                meta = repair_meta
                candidate, result, recommendation_dropped = _drop_redundant_recommendation(case, candidate, result)
                if recommendation_dropped:
                    status = "repaired_sanitized"
                    postprocess.append("blank_redundant_recommendation")
                candidate, result, name_stripped = _strip_repeated_option_name_prefix(case, candidate, result)
                if name_stripped:
                    status = "repaired_sanitized" if result["valid"] else status
                    postprocess.append("strip_repeated_option_name_prefix")
                candidate, result, investment_recommendation_dropped = _drop_investment_counter_recommendation(case, candidate, result)
                if investment_recommendation_dropped:
                    status = "repaired_sanitized" if result["valid"] else status
                    if "blank_investment_counter_recommendation" not in postprocess:
                        postprocess.append("blank_investment_counter_recommendation")
                candidate, result, wording_sanitized = _sanitize_internal_wording(case, candidate, result)
                if wording_sanitized:
                    status = "repaired_sanitized"
                    postprocess.append("sanitize_internal_wording")
                candidate, result, bureaucracy_sanitized = _sanitize_known_bureaucratic_wording(case, candidate, result)
                if bureaucracy_sanitized:
                    status = "repaired_sanitized"
                    postprocess.append("sanitize_known_bureaucratic_wording")
            else:
                result = {"valid": False, "errors": initial_errors + (repair_parse_errors or ["repair_gateway_not_ok"]), "text": "", "manual_review_required": True, "metadata": {}}
    return {
        "case": case_id,
        "ok": bool(result["valid"]),
        "network": True,
        "model": model,
        "temperature": temperature,
        "attempts": attempts,
        "status": status,
        "initial_errors": initial_errors,
        "postprocess": postprocess,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "errors": list(result["errors"]),
        "candidate": candidate,
        "text": result["text"],
        "manual_review_required": True,
        "metadata": result.get("metadata", {}),
        "gateway_meta": {"ok": True, "metadata_keys": sorted(meta.get("metadata_keys", []))[:20]},
    }


async def async_main() -> int:
    parser = argparse.ArgumentParser(description="Run one isolated shortlist Composer model call; no MCP or runtime changes.")
    parser.add_argument("--case", default=DEFAULT_CASE)
    parser.add_argument("--temperature", type=float, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    if not 0 <= args.temperature <= 1 or args.timeout <= 0:
        parser.error("temperature must be 0..1 and timeout must be positive")
    result = await run_live_case(args.case, temperature=args.temperature, model=args.model, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


def main() -> int:
    return asyncio.run(async_main())


if __name__ == "__main__":
    raise SystemExit(main())
