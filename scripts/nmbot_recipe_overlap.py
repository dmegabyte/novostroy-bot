#!/usr/bin/env python3
"""Manual local semantic overlap report for nmbot V2 recipe specs.

This command is intentionally explicit/manual: it reads the local recipe
registry, sends one batched embedding request to local loopback Ollama, and
prints review candidates. It never calls overlaps bugs or refactoring advice.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib import error, parse, request


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "nomic-embed-text:latest"
DEFAULT_THRESHOLD = 0.82
DEFAULT_TOP = 20
LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}
PAIR_FIELDS = (
    "stages",
    "viewpoints",
    "scopes",
    "card_mode",
    "fact_priority",
    "benefits",
    "forbidden",
    "cta_template",
    "reply_contract_id",
    "composition_mode",
)


class OverlapError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class EmbeddingClient(Protocol):
    def embed(self, inputs: list[str]) -> list[list[float]]:
        ...


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _strings(values: Any) -> list[str]:
    return [_enum_value(value) for value in tuple(values or ())]


def _mapping(value: Any) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, Mapping):
        return {}
    return {str(key): str(val) for key, val in value.items()}


def recipe_passport(recipe: Any) -> dict[str, Any]:
    """Return deterministic static fields used for exact and semantic review."""
    benefits = _mapping(getattr(recipe, "benefits", None))
    return {
        "id": str(getattr(recipe, "id")),
        "stages": _strings(getattr(recipe, "stages", ())),
        "viewpoints": _strings(getattr(recipe, "viewpoints", ())),
        "scopes": _strings(getattr(recipe, "scopes", ())),
        "card_mode": str(getattr(recipe, "card_mode", "")),
        "fact_priority": _strings(getattr(recipe, "fact_priority", ())),
        "benefits": {key: benefits[key] for key in sorted(benefits)},
        "forbidden": _strings(getattr(recipe, "forbidden", ())),
        "cta_template": str(getattr(recipe, "cta_template", "") or ""),
        "reply_contract_id": getattr(recipe, "reply_contract_id", None),
        "composition_mode": str(getattr(recipe, "composition_mode", "")),
    }


def passport_text(passport: Mapping[str, Any]) -> str:
    """Build stable semantic text; labels make field meaning transparent."""
    benefits = passport.get("benefits") or {}
    benefit_lines = [f"benefit:{key}={benefits[key]}" for key in sorted(benefits)]
    lines = [
        f"recipe_id:{passport['id']}",
        f"stages:{','.join(passport.get('stages') or [])}",
        f"viewpoints:{','.join(passport.get('viewpoints') or [])}",
        f"scopes:{','.join(passport.get('scopes') or [])}",
        f"card_mode:{passport.get('card_mode') or ''}",
        f"fact_priority:{','.join(passport.get('fact_priority') or [])}",
        *benefit_lines,
        f"forbidden:{','.join(passport.get('forbidden') or [])}",
        f"cta:{passport.get('cta_template') or ''}",
        f"reply_contract:{passport.get('reply_contract_id') or ''}",
        f"composition_mode:{passport.get('composition_mode') or ''}",
    ]
    return "\n".join(lines)


def exact_overlap(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, Any]:
    def common_list(name: str) -> list[str]:
        a = set(left.get(name) or [])
        b = set(right.get(name) or [])
        return sorted(a & b)

    left_benefits = set((left.get("benefits") or {}).keys())
    right_benefits = set((right.get("benefits") or {}).keys())
    cta_equal = bool(left.get("cta_template")) and left.get("cta_template") == right.get("cta_template")
    reply_equal = bool(left.get("reply_contract_id")) and left.get("reply_contract_id") == right.get("reply_contract_id")
    composition_equal = bool(left.get("composition_mode")) and left.get("composition_mode") == right.get("composition_mode")
    return {
        "stages": common_list("stages"),
        "viewpoints": common_list("viewpoints"),
        "scopes": common_list("scopes"),
        "fact_priority": common_list("fact_priority"),
        "benefit_keys": sorted(left_benefits & right_benefits),
        "forbidden": common_list("forbidden"),
        "reply_contract_equal": reply_equal,
        "cta_equal": cta_equal,
        "composition_mode_equal": composition_equal,
    }


def field_differences(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return deterministic field-level differences between two passports."""
    differences: dict[str, dict[str, Any]] = {}
    for field in PAIR_FIELDS:
        left_value = left.get(field)
        right_value = right.get(field)
        if left_value != right_value:
            differences[field] = {"left": left_value, "right": right_value}
    return differences


def test_references_for_pair(left_id: str, right_id: str, *, tests_dir: Path | None = None) -> list[str]:
    """Best-effort local textual scan for tests mentioning both recipe IDs."""
    root = tests_dir or ROOT / "tests"
    if not root.exists():
        return []
    left_pattern = re.compile(rf"(?<![\w.-]){re.escape(left_id)}(?![\w.-])")
    right_pattern = re.compile(rf"(?<![\w.-]){re.escape(right_id)}(?![\w.-])")
    references: list[str] = []
    for path in sorted(root.rglob("test_*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if left_pattern.search(text) and right_pattern.search(text):
            try:
                references.append(str(path.relative_to(ROOT)))
            except ValueError:
                references.append(str(path))
    return references


def cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        raise OverlapError("malformed_embedding", "embedding vectors must be non-empty and have equal dimensions")
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise OverlapError("malformed_embedding", "embedding vectors must not be zero vectors")
    return dot / (left_norm * right_norm)


def load_recipe_registry() -> dict[str, Any]:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from nmbot_v2.scenario_recipes import RECIPES  # local registry import only

    return dict(RECIPES)


def validate_loopback_host(host: str) -> str:
    parsed = parse.urlparse(host)
    if parsed.scheme != "http" or parsed.hostname not in LOOPBACK_HOSTS or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise OverlapError("unsafe_host", "host must be local loopback http://127.0.0.1:<port> or http://localhost:<port>")
    port = parsed.port or 11434
    if port < 1 or port > 65535:
        raise OverlapError("unsafe_host", "host port must be in 1..65535")
    return f"http://{parsed.hostname}:{port}"


class OllamaEmbeddingClient:
    def __init__(self, *, host: str = DEFAULT_HOST, model: str = DEFAULT_MODEL, timeout: float = 30.0) -> None:
        self.host = validate_loopback_host(host)
        self.model = model
        self.timeout = timeout

    def embed(self, inputs: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model, "input": inputs}, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.host}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read()
        except (OSError, error.URLError) as exc:
            raise OverlapError("ollama_unavailable", "local Ollama embedding endpoint is unavailable") from exc
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OverlapError("malformed_ollama_response", "local Ollama embedding response is not valid JSON") from exc
        embeddings = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(inputs):
            raise OverlapError("malformed_ollama_response", "local Ollama embedding response has unexpected embeddings shape")
        out: list[list[float]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in vector):
                raise OverlapError("malformed_ollama_response", "local Ollama embedding vector has unexpected shape")
            out.append([float(x) for x in vector])
        return out


def build_report(*, recipes: Mapping[str, Any], embedding_client: EmbeddingClient, threshold: float = DEFAULT_THRESHOLD, top: int = DEFAULT_TOP, model: str = DEFAULT_MODEL) -> dict[str, Any]:
    passports = [recipe_passport(recipe) for _, recipe in sorted(recipes.items())]
    texts = [passport_text(passport) for passport in passports]
    embeddings = embedding_client.embed(texts)
    if len(embeddings) != len(passports):
        raise OverlapError("malformed_embedding", "embedding client returned a different number of vectors")

    pairs: list[dict[str, Any]] = []
    for i, left in enumerate(passports):
        for j in range(i + 1, len(passports)):
            right = passports[j]
            score = cosine(embeddings[i], embeddings[j])
            pairs.append(
                {
                    "left_id": left["id"],
                    "right_id": right["id"],
                    "semantic_score": round(score, 6),
                    "threshold": threshold,
                    "label": "needs_review" if score >= threshold else "below_threshold",
                    "candidate_only": True,
                    "exact_overlap": exact_overlap(left, right),
                }
            )
    pairs.sort(key=lambda item: (-float(item["semantic_score"]), str(item["left_id"]), str(item["right_id"])))
    return {
        "schema_version": 1,
        "source": "nmbot_v2.scenario_recipes.RECIPES",
        "model": model,
        "threshold": threshold,
        "recipe_count": len(passports),
        "pair_count": len(pairs),
        "candidate_policy": "Semantic scores and static overlaps are manual review candidates only; they are not bugs, deletion recommendations, production proof, or automatic refactoring advice.",
        "pairs": pairs[:top],
    }


def build_pair_report(*, recipes: Mapping[str, Any], left_id: str, right_id: str) -> dict[str, Any]:
    """Build a local-only deterministic overlap card for exactly two recipes."""
    missing = [recipe_id for recipe_id in (left_id, right_id) if recipe_id not in recipes]
    if missing:
        raise OverlapError("unknown_recipe_id", f"unknown recipe id: {', '.join(missing)}")
    left = recipe_passport(recipes[left_id])
    right = recipe_passport(recipes[right_id])
    return {
        "schema_version": 1,
        "source": "nmbot_v2.scenario_recipes.RECIPES",
        "mode": "pair",
        "candidate_policy": "This local card is a manual review candidate only; it is not a bug, deletion recommendation, production proof, or automatic refactoring instruction.",
        "left_id": left["id"],
        "right_id": right["id"],
        "passports": {"left": left, "right": right},
        "exact_overlap": exact_overlap(left, right),
        "field_differences": field_differences(left, right),
        "test_references": test_references_for_pair(left["id"], right["id"]),
    }


def _shared_static_facts(exact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "shared_stage": exact.get("stages") or [],
        "shared_viewpoint": exact.get("viewpoints") or [],
        "shared_scope": exact.get("scopes") or [],
        "shared_forbidden": exact.get("forbidden") or [],
        "shared_reply_contract": bool(exact.get("reply_contract_equal")),
        "shared_cta": bool(exact.get("cta_equal")),
    }


def _concrete_differences(differences: Mapping[str, Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    selected: dict[str, Mapping[str, Any]] = {}
    for field in ("scopes", "stages", "cta_template", "fact_priority"):
        if field in differences:
            selected[field] = differences[field]
    return selected


def _plain_conclusion(*, left_id: str, right_id: str, shared: Mapping[str, Any], concrete_differences: Mapping[str, Any]) -> str:
    shared_bits: list[str] = []
    for label, key in (("stage", "shared_stage"), ("viewpoint", "shared_viewpoint"), ("scope", "shared_scope"), ("forbidden rules", "shared_forbidden")):
        values = shared.get(key) or []
        if values:
            shared_bits.append(f"shared {label}: {_md_value(values)}")
    if shared.get("shared_reply_contract"):
        shared_bits.append("same reply contract")
    if shared.get("shared_cta"):
        shared_bits.append("same CTA text")
    diff_bits = [field for field in ("stages", "scopes", "cta_template", "fact_priority") if field in concrete_differences]
    shared_text = "; ".join(shared_bits) if shared_bits else "no populated exact shared fields in the reviewed static fields"
    diff_text = ", ".join(diff_bits) if diff_bits else "no concrete differences in stage/scope/CTA/fact priority"
    return f"`{left_id}` and `{right_id}` are a manual review candidate based only on local static recipe facts: {shared_text}. Concrete differences to inspect: {diff_text}. This is navigation only, not a merge recommendation or defect claim."


def build_explain_report(*, recipes: Mapping[str, Any], left_id: str, right_id: str) -> dict[str, Any]:
    """Build a deterministic local Markdown/JSON review card from the pair report."""
    pair = build_pair_report(recipes=recipes, left_id=left_id, right_id=right_id)
    exact = pair.get("exact_overlap") or {}
    differences = pair.get("field_differences") or {}
    shared = _shared_static_facts(exact)
    concrete = _concrete_differences(differences)
    return {
        "schema_version": 1,
        "source": pair["source"],
        "mode": "explain",
        "candidate_policy": pair["candidate_policy"],
        "left_id": pair["left_id"],
        "right_id": pair["right_id"],
        "plain_language_conclusion": _plain_conclusion(left_id=pair["left_id"], right_id=pair["right_id"], shared=shared, concrete_differences=concrete),
        "shared_static_facts": shared,
        "concrete_differences": concrete,
        "manual_decision_checklist": [
            "Do both recipes answer the same user intent after reading the full source recipes?",
            "Are the different stages/scopes intentional routing boundaries?",
            "Would a shared implementation preserve CTA wording, fact priority, forbidden inferences, and reply contract behaviour?",
            "Which local tests should be inspected before deciding keep-separate versus assess-consolidation?",
        ],
        "local_textual_test_references": pair.get("test_references") or [],
        "test_reference_policy": "Navigation only: these paths only mention both recipe IDs textually and do not prove behavioural coverage.",
        "candidate_only_boundary": "Manual local review candidate only; not a bug report, merge recommendation, deletion recommendation, production proof, or automatic refactoring instruction.",
    }


def print_human(report: Mapping[str, Any]) -> None:
    print("Recipe semantic overlap report")
    print(f"source: {report['source']}")
    print(f"model: {report['model']}")
    print(f"threshold: {report['threshold']}")
    print("policy: candidates only; no bug/deletion/prod/refactor claims")
    for item in report.get("pairs", []):
        print(f"- {item['left_id']} ↔ {item['right_id']}: score={item['semantic_score']} label={item['label']}")
        exact = item.get("exact_overlap") or {}
        populated = {key: value for key, value in exact.items() if value}
        if populated:
            print(f"  exact_overlap: {json.dumps(populated, ensure_ascii=False, sort_keys=True)}")


def _md_value(value: Any) -> str:
    if value in (None, "", [], {}, ()):
        return "—"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "—"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def print_pair_markdown(report: Mapping[str, Any]) -> None:
    print(f"# Recipe overlap pair: `{report['left_id']}` ↔ `{report['right_id']}`")
    print()
    print(f"Source: `{report['source']}`")
    print()
    print("> Candidate-only boundary: this card is for manual local review. It is not a bug report, deletion recommendation, production proof, or automatic refactoring instruction.")
    print()
    passports = report.get("passports") or {}
    left = passports.get("left") or {}
    right = passports.get("right") or {}
    print("## Passport")
    print()
    print("| Field | Left | Right |")
    print("|---|---|---|")
    for field in ("id", *PAIR_FIELDS):
        print(f"| `{field}` | {_md_value(left.get(field))} | {_md_value(right.get(field))} |")
    print()
    print("## Shared exact overlap")
    print()
    exact = report.get("exact_overlap") or {}
    populated = {key: value for key, value in exact.items() if value}
    if populated:
        for key in sorted(populated):
            print(f"- `{key}`: {_md_value(populated[key])}")
    else:
        print("- No populated exact-overlap fields.")
    print()
    print("## Field-level differences")
    print()
    differences = report.get("field_differences") or {}
    if differences:
        print("| Field | Left | Right |")
        print("|---|---|---|")
        for field in sorted(differences):
            diff = differences[field]
            print(f"| `{field}` | {_md_value(diff.get('left'))} | {_md_value(diff.get('right'))} |")
    else:
        print("- No field-level differences in the static passport fields.")
    print()
    print("## Test references from local textual scan")
    print()
    refs = report.get("test_references") or []
    if refs:
        for ref in refs:
            print(f"- `{ref}`")
    else:
        print("- No local test file was safely identified as mentioning both recipe IDs.")


def print_explain_markdown(report: Mapping[str, Any]) -> None:
    print(f"# Recipe explain: `{report['left_id']}` ↔ `{report['right_id']}`")
    print()
    print(f"Source: `{report['source']}`")
    print()
    print(f"> Candidate-only boundary: {report['candidate_only_boundary']}")
    print()
    print("## Plain-language conclusion")
    print()
    print(report["plain_language_conclusion"])
    print()
    print("## Shared static facts")
    print()
    shared = report.get("shared_static_facts") or {}
    for key in ("shared_stage", "shared_viewpoint", "shared_scope", "shared_forbidden", "shared_reply_contract", "shared_cta"):
        print(f"- `{key}`: {_md_value(shared.get(key))}")
    print()
    print("## Concrete differences to inspect")
    print()
    differences = report.get("concrete_differences") or {}
    if differences:
        print("| Field | Left | Right |")
        print("|---|---|---|")
        for field in ("stages", "scopes", "cta_template", "fact_priority"):
            if field in differences:
                diff = differences[field]
                print(f"| `{field}` | {_md_value(diff.get('left'))} | {_md_value(diff.get('right'))} |")
    else:
        print("- No differences in stage, scope, CTA, or fact priority were found in the static passports.")
    print()
    print("## Manual decision checklist: keep separate vs assess consolidation")
    print()
    for item in report.get("manual_decision_checklist") or []:
        print(f"- {item}")
    print()
    print("## Local textual test references — navigation only")
    print()
    refs = report.get("local_textual_test_references") or []
    if refs:
        for ref in refs:
            print(f"- `{ref}`")
    else:
        print("- No local test file was safely identified as mentioning both recipe IDs.")
    print()
    print(f"Policy: {report['test_reference_policy']}")


def error_payload(exc: OverlapError) -> dict[str, Any]:
    return {"ok": False, "error": {"code": exc.code, "message": exc.message}}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manual local semantic recipe-overlap review report.")
    parser.add_argument("--json", action="store_true", help="Print JSON (default).")
    parser.add_argument("--human", action="store_true", help="Print concise human report.")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    parser.add_argument("--pair", nargs=2, metavar=("RECIPE_A", "RECIPE_B"), help="Print a local-only deterministic pair report without calling Ollama.")
    parser.add_argument("--explain", nargs=2, metavar=("RECIPE_A", "RECIPE_B"), help="Print a local-only deterministic Markdown/JSON review card from the pair report without calling Ollama.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.pair and args.explain:
            raise OverlapError("ambiguous_mode", "choose only one of --pair or --explain")
        if args.explain:
            report = build_explain_report(recipes=load_recipe_registry(), left_id=args.explain[0], right_id=args.explain[1])
            if args.human:
                print_explain_markdown(report)
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.pair:
            report = build_pair_report(recipes=load_recipe_registry(), left_id=args.pair[0], right_id=args.pair[1])
            if args.human:
                print_pair_markdown(report)
            else:
                print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if not 0.0 <= args.threshold <= 1.0:
            raise OverlapError("invalid_threshold", "threshold must be in 0..1")
        if args.top < 1:
            raise OverlapError("invalid_top", "top must be at least 1")
        client = OllamaEmbeddingClient(host=args.host, model=args.model)
        report = build_report(recipes=load_recipe_registry(), embedding_client=client, threshold=args.threshold, top=args.top, model=args.model)
    except OverlapError as exc:
        print(json.dumps(error_payload(exc), ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    if args.human:
        print_human(report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
