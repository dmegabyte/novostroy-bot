#!/usr/bin/env python3
"""Promptfoo assertion for nmbot live answer quality rows.

This assertion intentionally works on already-produced answers. In preview mode
the provider is `echo`, so promptfoo spends no model tokens: it simply feeds the
saved response into this checker.
"""

from __future__ import annotations

import json
import re
from typing import Any


HARD_WARNING_PREFIXES = (
    "typo_",
    "forbidden_word:",
)

WATCH_WARNING_PREFIXES = (
    "facts_visible_mismatch",
    "empty_why_without_evidence",
    "reason_too_short",
)

TECH_TERMS = ("mcp", "json", "regex")


def _split_warnings(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in str(raw).split(";") if x.strip()]


def _load_pm(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _scenario_quality_penalties(scenario: str, response: str) -> list[str]:
    low = response.lower()
    issues: list[str] = []

    if scenario == "family" and not any(token in low for token in ("школ", "сад", "парк", "двор")):
        issues.append("family_missing_family_facts")
    if scenario == "investment" and not any(token in low for token in ("вход", "ипотек", "сдел", "скид", "ставк", "взнос")):
        issues.append("investment_missing_investment_reason")
    if scenario == "rental" and not any(token in low for token in ("аренд", "студи", "одноком", "отделк", "метро", "готов", "сдан")):
        issues.append("rental_missing_rentability_reason")
    if scenario == "repeat_search" and not any(token in low for token in ("друг", "нов", "ещё", "еще", "повтор", "другие", "новые")):
        issues.append("repeat_search_not_fresh")
    if scenario == "default":
        if any(token in low for token in ("1.", "2.", "жк", "вариант", "shortlist")):
            issues.append("default_should_not_show_list")
        if len(re.findall(r"\?", response)) != 1:
            issues.append("default_question_count_not_one")
    if scenario == "off_topic" and not any(token in low for token in ("недвиж", "новостро", "подбор")):
        issues.append("offtopic_missing_boundary")

    if any(term in low for term in TECH_TERMS):
        issues.append("technical_term_leak")
    if len(re.findall(r"\?", response)) > 1:
        issues.append("too_many_questions")
    if not response.strip():
        issues.append("empty_response")

    return issues


def get_assert(output: str, context: dict[str, Any]) -> dict[str, Any]:
    vars_dict = context.get("vars", {}) or {}
    response = str(vars_dict.get("response") or output or "")
    scenario = str(vars_dict.get("case") or "").strip()
    warnings = _split_warnings(vars_dict.get("warnings"))
    pm = _load_pm(vars_dict.get("prompt_master_verdict"))

    reasons: list[str] = []
    score = 1.0

    hard_warnings = [w for w in warnings if w.startswith(HARD_WARNING_PREFIXES)]
    if hard_warnings:
        score -= 0.35
        reasons.append("hard warnings: " + ", ".join(hard_warnings))

    watch_warnings = [w for w in warnings if w.startswith(WATCH_WARNING_PREFIXES)]
    if watch_warnings:
        score -= 0.20
        reasons.append("watch warnings: " + ", ".join(watch_warnings))

    pm_score = pm.get("score")
    pm_verdict = str(pm.get("verdict") or "").lower()
    if isinstance(pm_score, (int, float)):
        score = min(score, max(0.0, float(pm_score) / 100.0))
    if pm_verdict == "bad":
        score -= 0.20
        reasons.append("promptmaster-style verdict is bad")
    elif pm_verdict == "watch":
        reasons.append("promptmaster-style verdict is watch")

    scenario_issues = _scenario_quality_penalties(scenario, response)
    if scenario_issues:
        score -= 0.20
        reasons.append("scenario issues: " + ", ".join(scenario_issues))

    score = max(0.0, min(1.0, score))
    passed = score >= 0.75 and not hard_warnings and pm_verdict != "bad"
    if not reasons:
        reasons.append("answer passed local regression checks")

    return {
        "pass": passed,
        "score": score,
        "reason": "; ".join(reasons),
        "named_scores": {
            "warnings_count": len(warnings),
            "prompt_master_score": pm_score if isinstance(pm_score, (int, float)) else -1,
            "scenario_issue_count": len(scenario_issues),
        },
    }
