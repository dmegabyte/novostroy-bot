from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_recipe_overlap.py"


def load_module():
    spec = importlib.util.spec_from_file_location("nmbot_recipe_overlap_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


@dataclass(frozen=True)
class Recipe:
    id: str
    stages: tuple[str, ...]
    viewpoints: tuple[str, ...] = ()
    scopes: tuple[str, ...] = ()
    card_mode: str = "normal"
    fact_priority: tuple[str, ...] = ()
    benefits: dict[str, str] | None = None
    forbidden: tuple[str, ...] = ()
    cta_template: str = ""
    reply_contract_id: str | None = None
    composition_mode: str = "bounded"


class FakeEmbeddings:
    def __init__(self, vectors):
        self.vectors = vectors
        self.inputs = None

    def embed(self, inputs):
        self.inputs = inputs
        return self.vectors


def test_passport_exact_overlap_and_candidate_only_report_ordering() -> None:
    mod = load_module()
    recipes = {
        "b": Recipe("b", ("selected_object",), scopes=("one",), fact_priority=("metro",), benefits={"metro": "рядом"}, forbidden=("бронь",), cta_template="Проверить?", reply_contract_id="contract"),
        "a": Recipe("a", ("selected_object",), scopes=("one",), fact_priority=("metro", "price"), benefits={"metro": "рядом", "price": "цена"}, forbidden=("бронь",), cta_template="Проверить?", reply_contract_id="contract"),
        "c": Recipe("c", ("off_topic",), composition_mode="deterministic"),
    }
    fake = FakeEmbeddings([[1.0, 0.0], [1.0, 0.0], [0.0, 1.0]])

    report = mod.build_report(recipes=recipes, embedding_client=fake, threshold=0.9, top=3, model="fake")

    assert report["candidate_policy"].startswith("Semantic scores")
    assert fake.inputs and fake.inputs[0].startswith("recipe_id:a")
    assert [(p["left_id"], p["right_id"], p["semantic_score"], p["label"], p["candidate_only"]) for p in report["pairs"]] == [
        ("a", "b", 1.0, "needs_review", True),
        ("a", "c", 0.0, "below_threshold", True),
        ("b", "c", 0.0, "below_threshold", True),
    ]
    exact = report["pairs"][0]["exact_overlap"]
    assert exact["stages"] == ["selected_object"]
    assert exact["scopes"] == ["one"]
    assert exact["fact_priority"] == ["metro"]
    assert exact["benefit_keys"] == ["metro"]
    assert exact["forbidden"] == ["бронь"]
    assert exact["reply_contract_equal"] is True
    assert exact["cta_equal"] is True


def test_cosine_rejects_bad_vectors_and_zero_vectors() -> None:
    mod = load_module()
    assert round(mod.cosine([1, 1], [1, 0]), 6) == 0.707107
    try:
        mod.cosine([0, 0], [1, 0])
    except mod.OverlapError as exc:
        assert exc.code == "malformed_embedding"
    else:  # pragma: no cover
        raise AssertionError("zero vector must fail")


def test_host_rejection_is_loopback_only() -> None:
    mod = load_module()
    assert mod.validate_loopback_host("http://localhost:11434") == "http://localhost:11434"
    assert mod.validate_loopback_host("http://127.0.0.1") == "http://127.0.0.1:11434"
    for host in ("https://127.0.0.1:11434", "http://0.0.0.0:11434", "http://example.com:11434", "http://user@localhost:11434"):
        try:
            mod.validate_loopback_host(host)
        except mod.OverlapError as exc:
            assert exc.code == "unsafe_host"
        else:  # pragma: no cover
            raise AssertionError(f"host must be rejected: {host}")


def test_unavailable_client_returns_safe_error(monkeypatch, capsys) -> None:
    mod = load_module()

    class BadClient:
        def __init__(self, **kwargs):
            pass

        def embed(self, inputs):
            raise mod.OverlapError("ollama_unavailable", "local Ollama embedding endpoint is unavailable")

    monkeypatch.setattr(mod, "OllamaEmbeddingClient", BadClient)
    monkeypatch.setattr(mod, "load_recipe_registry", lambda: {"a": Recipe("a", ("x",)), "b": Recipe("b", ("x",))})
    assert mod.main(["--json"]) == 2
    captured = capsys.readouterr()
    assert "ollama_unavailable" in captured.err
    assert "127.0.0.1" not in captured.err


def test_pair_report_is_deterministic_markdown_and_local_only(monkeypatch, capsys) -> None:
    mod = load_module()
    recipes = {
        "a": Recipe("a", ("selected_object",), scopes=("one",), fact_priority=("metro", "price"), benefits={"metro": "рядом", "price": "цена"}, forbidden=("бронь",), cta_template="Проверить?", reply_contract_id="contract"),
        "b": Recipe("b", ("selected_object",), scopes=("one",), fact_priority=("metro",), benefits={"metro": "рядом"}, forbidden=("бронь",), cta_template="Проверить?", reply_contract_id="contract"),
    }

    class MustNotInstantiateClient:
        def __init__(self, **kwargs):  # pragma: no cover - pair mode must not call embeddings
            raise AssertionError("pair mode must not instantiate Ollama client")

    monkeypatch.setattr(mod, "OllamaEmbeddingClient", MustNotInstantiateClient)
    monkeypatch.setattr(mod, "load_recipe_registry", lambda: recipes)
    monkeypatch.setattr(mod, "test_references_for_pair", lambda left, right: ["tests/test_pair_example.py"])

    assert mod.main(["--pair", "a", "b", "--human"]) == 0
    captured = capsys.readouterr()
    assert "# Recipe overlap pair: `a` ↔ `b`" in captured.out
    assert "Candidate-only boundary" in captured.out
    assert "`benefit_keys`: metro" in captured.out
    assert "| `fact_priority` | metro, price | metro |" in captured.out
    assert "`tests/test_pair_example.py`" in captured.out

    report = mod.build_pair_report(recipes=recipes, left_id="a", right_id="b")
    assert report["mode"] == "pair"
    assert report["passports"]["left"]["id"] == "a"
    assert report["exact_overlap"]["benefit_keys"] == ["metro"]
    assert report["field_differences"]["fact_priority"] == {"left": ["metro", "price"], "right": ["metro"]}


def test_pair_rejects_unknown_id_before_embedding_client(monkeypatch, capsys) -> None:
    mod = load_module()

    class MustNotInstantiateClient:
        def __init__(self, **kwargs):  # pragma: no cover - unknown pair must not call embeddings
            raise AssertionError("pair mode must not instantiate Ollama client")

    monkeypatch.setattr(mod, "OllamaEmbeddingClient", MustNotInstantiateClient)
    monkeypatch.setattr(mod, "load_recipe_registry", lambda: {"a": Recipe("a", ("x",))})
    assert mod.main(["--pair", "a", "missing", "--json"]) == 2
    captured = capsys.readouterr()
    assert "unknown_recipe_id" in captured.err
    assert "missing" in captured.err


def test_explain_report_static_sections_and_local_only(monkeypatch, capsys) -> None:
    mod = load_module()
    recipes = {
        "a": Recipe("a", ("selected_object",), viewpoints=("financing",), scopes=("one",), fact_priority=("metro", "price"), forbidden=("бронь",), cta_template="Проверить этот ЖК?", reply_contract_id="contract"),
        "b": Recipe("b", ("financing_clarification",), viewpoints=("financing",), scopes=("all",), fact_priority=("metro",), forbidden=("бронь",), cta_template="Проверить все ЖК?", reply_contract_id="contract"),
    }

    class MustNotInstantiateClient:
        def __init__(self, **kwargs):  # pragma: no cover - explain mode must not call embeddings
            raise AssertionError("explain mode must not instantiate Ollama client")

    monkeypatch.setattr(mod, "OllamaEmbeddingClient", MustNotInstantiateClient)
    monkeypatch.setattr(mod, "load_recipe_registry", lambda: recipes)
    monkeypatch.setattr(mod, "test_references_for_pair", lambda left, right: ["tests/test_pair_example.py"])

    assert mod.main(["--explain", "a", "b", "--human"]) == 0
    captured = capsys.readouterr()
    assert "# Recipe explain: `a` ↔ `b`" in captured.out
    assert "## Plain-language conclusion" in captured.out
    assert "## Shared static facts" in captured.out
    assert "## Concrete differences to inspect" in captured.out
    assert "## Manual decision checklist: keep separate vs assess consolidation" in captured.out
    assert "## Local textual test references — navigation only" in captured.out
    assert "not a bug report, merge recommendation" in captured.out
    assert "| `stages` | selected_object | financing_clarification |" in captured.out
    assert "`tests/test_pair_example.py`" in captured.out

    report = mod.build_explain_report(recipes=recipes, left_id="a", right_id="b")
    assert report["mode"] == "explain"
    assert report["shared_static_facts"]["shared_viewpoint"] == ["financing"]
    assert report["shared_static_facts"]["shared_reply_contract"] is True
    assert set(report["concrete_differences"]) == {"scopes", "stages", "cta_template", "fact_priority"}


def test_explain_rejects_unknown_id_before_embedding_client(monkeypatch, capsys) -> None:
    mod = load_module()

    class MustNotInstantiateClient:
        def __init__(self, **kwargs):  # pragma: no cover - unknown explain must not call embeddings
            raise AssertionError("explain mode must not instantiate Ollama client")

    monkeypatch.setattr(mod, "OllamaEmbeddingClient", MustNotInstantiateClient)
    monkeypatch.setattr(mod, "load_recipe_registry", lambda: {"a": Recipe("a", ("x",))})
    assert mod.main(["--explain", "a", "missing", "--json"]) == 2
    captured = capsys.readouterr()
    assert "unknown_recipe_id" in captured.err
    assert "missing" in captured.err


def test_pair_and_explain_together_is_rejected_before_embedding_client(monkeypatch, capsys) -> None:
    mod = load_module()

    class MustNotInstantiateClient:
        def __init__(self, **kwargs):  # pragma: no cover - ambiguous local modes must not call embeddings
            raise AssertionError("ambiguous mode must not instantiate Ollama client")

    monkeypatch.setattr(mod, "OllamaEmbeddingClient", MustNotInstantiateClient)
    assert mod.main(["--pair", "a", "b", "--explain", "a", "b", "--json"]) == 2
    captured = capsys.readouterr()
    assert "ambiguous_mode" in captured.err
