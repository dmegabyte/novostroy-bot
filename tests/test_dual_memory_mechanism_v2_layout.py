from __future__ import annotations

import importlib.util
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT = PROJECT_ROOT / "experiments" / "dual_memory_sandbox" / "mechanism_v2"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_validator():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("mechanism_v2_validate_layout", ROOT / "validate_layout.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_validator_for_copy(copied_root: Path):
    spec = importlib.util.spec_from_file_location("mechanism_v2_validate_layout_copy", copied_root / "validate_layout.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.ROOT = copied_root
    return module


def load_preparer():
    spec = importlib.util.spec_from_file_location("mechanism_v2_prepare_run", ROOT / "prepare_run.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_validator_accepts_static_layout():
    validator = load_validator()
    errors, manifest = validator.validate()
    assert errors == []
    assert "experiment.json" in manifest
    assert "public/tasks.jsonl" in manifest
    assert "private/labels.jsonl" in manifest
    assert "private/advisory_payloads.jsonl" in manifest


def test_counts_families_and_public_private_ids():
    tasks = read_jsonl(ROOT / "public" / "tasks.jsonl")
    labels = read_jsonl(ROOT / "private" / "labels.jsonl")
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    assert len(tasks) == 15
    assert sum(1 for row in tasks if row["partition"] == "learning") == 9
    assert sum(1 for row in tasks if row["partition"] == "holdout") == 6
    assert {row["task_id"] for row in labels} == {row["task_id"] for row in tasks}
    assert set(experiment["arms"]) == {"B0", "M1", "S1"}
    by_family = Counter((row["family_id"], row["partition"]) for row in tasks)
    for family in {"normalize", "cache", "boundary"}:
        assert by_family[(family, "learning")] == 3
        assert by_family[(family, "holdout")] == 2


def test_counterbalanced_schedule_and_disjoint_sham_advice():
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    advice = experiment["advice_catalog"]
    schedule = experiment["counterbalanced_schedule"]
    assert len(schedule) == 6
    assert {row["task_id"] for row in schedule} == set(experiment["holdout_task_ids"])
    position_counts = Counter()
    for row in schedule:
        assert set(row["arm_order"]) == {"B0", "M1", "S1"}
        assert row["m1_advice_family"] == row["family_id"]
        assert row["s1_sham_family"] != row["family_id"]
        assert set(advice[row["m1_advice_family"]]).isdisjoint(advice[row["s1_sham_family"]])
        assert experiment["receipt_advice_allowlist"][row["task_id"]] == {
            "B0": [],
            "M1": advice[row["m1_advice_family"]],
            "S1": advice[row["s1_sham_family"]],
        }
        for pos, arm in enumerate(row["arm_order"]):
            position_counts[(pos, arm)] += 1
    for pos in range(3):
        assert {position_counts[(pos, arm)] for arm in {"B0", "M1", "S1"}} == {2}


def test_receipt_schema_is_closed_and_observational():
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    receipt = experiment["receipt_schema"]
    assert set(receipt["closed_keys"]) == {"task_id", "arm", "consulted_advice_codes", "selected_check_codes", "receipt_version"}
    assert receipt["receipt_version"] == "mechanism-v2-receipt-1"
    assert experiment["claim_boundary"]["causal_claim_allowed"] is False
    assert "self-report" in experiment["claim_boundary"]["reason"]
    assert experiment["outcomes"]["no_composite_metric"] is True
    assert "mechanism_not_evaluable" in experiment["outcomes"]["mechanism_evidence_rule"]


def test_holdout_public_cards_use_arm_specific_advice_allowlist():
    tasks = read_jsonl(ROOT / "public" / "tasks.jsonl")
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    for row in tasks:
        allowed = row["allowed_advice_codes"]
        if row["partition"] == "learning":
            assert isinstance(allowed, list)
            assert set(allowed) == set(experiment["advice_catalog"][row["family_id"]])
        else:
            assert set(allowed) == {"B0", "M1", "S1"}
            assert allowed == experiment["receipt_advice_allowlist"][row["task_id"]]
            assert allowed["B0"] == []
            assert set(allowed["M1"]).isdisjoint(allowed["S1"])


def test_holdout_public_cards_have_safe_route_scope():
    tasks = read_jsonl(ROOT / "public" / "tasks.jsonl")
    holdouts = [row for row in tasks if row["partition"] == "holdout"]
    assert len(holdouts) == 6
    forbidden = {
        "expected",
        "answer",
        "assertion",
        "label",
        "private",
        "hidden",
        "adv-",
        "check-",
        "selected_expected_route",
        "expected_family_route",
    }
    for row in holdouts:
        route_scope = row.get("route_scope")
        assert isinstance(route_scope, str)
        assert len(route_scope.strip()) >= 40
        lowered = route_scope.lower()
        assert "in scope" in lowered
        assert "out of scope" in lowered
        assert not any(token in lowered for token in forbidden)


def test_validator_rejects_holdout_missing_route_scope(tmp_path):
    copied_root = tmp_path / "mechanism_v2_copy"
    shutil.copytree(ROOT, copied_root)
    tasks_path = copied_root / "public" / "tasks.jsonl"
    tasks = read_jsonl(tasks_path)
    holdout = next(row for row in tasks if row["partition"] == "holdout")
    task_id = holdout["task_id"]
    del holdout["route_scope"]
    tasks_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in tasks) + "\n", encoding="utf-8")

    validator = load_validator_for_copy(copied_root)
    errors, _manifest = validator.validate()

    assert f"task {task_id} has non-closed keys" in errors
    assert f"task {task_id} holdout route_scope must be nonempty public prose" in errors


def test_validator_rejects_holdout_route_scope_forbidden_private_token(tmp_path):
    copied_root = tmp_path / "mechanism_v2_copy"
    shutil.copytree(ROOT, copied_root)
    tasks_path = copied_root / "public" / "tasks.jsonl"
    tasks = read_jsonl(tasks_path)
    holdout = next(row for row in tasks if row["partition"] == "holdout")
    task_id = holdout["task_id"]
    holdout["route_scope"] = (
        "In scope: public triage dimensions and user-visible routing boundaries. "
        "Out of scope: private label wording or check-code advice hints."
    )
    tasks_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in tasks) + "\n", encoding="utf-8")

    validator = load_validator_for_copy(copied_root)
    errors, _manifest = validator.validate()

    assert f"task {task_id} route_scope leaks forbidden/private term: private" in errors
    assert f"task {task_id} route_scope leaks forbidden/private term: label" in errors
    assert f"task {task_id} route_scope leaks forbidden/private term: check-" in errors


def test_static_advisory_payload_manifest_is_complete_safe_and_hash_locked():
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    payload_path = ROOT / experiment["advisory_payload_manifest"]["path"]
    payloads = read_jsonl(payload_path)
    payload_hash = hashlib.sha256(payload_path.read_bytes()).hexdigest()
    assert payload_hash == experiment["advisory_payload_manifest"]["sha256"]

    schedule = {row["task_id"]: row for row in experiment["counterbalanced_schedule"]}
    by_pair = {(row["task_id"], row["arm"]): row for row in payloads}
    assert len(payloads) == 12
    for task_id, scheduled in schedule.items():
        assert (task_id, "B0") not in by_pair
        m1 = by_pair[(task_id, "M1")]
        s1 = by_pair[(task_id, "S1")]
        assert m1["task_family"] == scheduled["family_id"]
        assert s1["task_family"] == scheduled["family_id"]
        assert m1["advice_family"] == scheduled["m1_advice_family"]
        assert s1["advice_family"] == scheduled["s1_sham_family"]
        assert m1["advice_family"] != s1["advice_family"]
        assert [entry["code"] for entry in m1["entries"]] == experiment["receipt_advice_allowlist"][task_id]["M1"]
        assert [entry["code"] for entry in s1["entries"]] == experiment["receipt_advice_allowlist"][task_id]["S1"]
        assert len(m1["entries"]) == len(s1["entries"]) == 3
        m1_len = sum(len(entry["code"]) for entry in m1["entries"])
        s1_len = sum(len(entry["code"]) for entry in s1["entries"])
        assert abs(m1_len - s1_len) <= 12
        for payload in (m1, s1):
            assert payload["arm"] in {"M1", "S1"}
            for entry in payload["entries"]:
                assert set(entry) == {"code", "family", "safe_summary"}
                assert entry["family"] == payload["advice_family"]
                lowered = entry["safe_summary"].lower()
                assert not any(token in lowered for token in ["prompt", "thought", "code", "log", "label", "outcome"])


def test_public_surface_does_not_expose_private_labels_or_expected_answers():
    public_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "public").rglob("*"))
        if path.is_file()
    ).lower()
    labels = read_jsonl(ROOT / "private" / "labels.jsonl")
    forbidden = ["expected_answer", "hidden_assertions", "quality_label_placeholder", "private_expected_check_codes"]
    for token in forbidden:
        assert token not in public_text
    for label in labels:
        assert label["quality_label_placeholder"].lower() not in public_text
        for check in label["private_expected_check_codes"]:
            # Public cards may contain family-level allowed checks, but not the
            # private expected check pair as a single leaked label value.
            assert " ".join(label["private_expected_check_codes"]).lower() not in public_text


def test_boundaries_are_static_only_no_runner():
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    assert experiment["status"] == "PREPARED_NOT_RUN"
    assert experiment["boundaries"]["static_only_now"] is True
    forbidden = set(experiment["boundaries"]["forbidden_now"])
    assert {"agents", "models", "providers", "fixtures", "hidden_verifier", "scorer", "eval", "network", "VPS", "production"}.issubset(forbidden)


def test_execution_boundary_contract_is_preparation_only_and_arm_sliced():
    experiment = json.loads((ROOT / "experiment.json").read_text(encoding="utf-8"))
    boundary = experiment["execution_boundary"]
    assert boundary["preparation_only"] is True
    assert boundary["deny_execution_by_default"] is True
    assert boundary["agent_packet_source"] == "arm_sliced_preparer_only"
    assert boundary["runs_root_required"] is True
    assert boundary["workspace_fresh_required"] is True
    assert set(boundary["agent_packet_files"]) == {"agent_packet.json", "run_manifest.json", "RECEIPT_SCHEMA.json"}
    assert {"private_labels", "other_arm_payloads", "full_arm_map", "schedule", "hidden_outcomes", "raw_prompt", "thought", "log", "code"}.issubset(boundary["agent_packet_forbidden"])


def test_preparer_b0_packet_has_no_advice_codes_or_payload():
    preparer = load_preparer()
    packet, manifest = preparer.build_packet("mech-normalize-holdout-01", "B0")
    packet_text = json.dumps(packet, sort_keys=True)
    all_tasks = read_jsonl(ROOT / "public" / "tasks.jsonl")
    own_task = next(row for row in all_tasks if row["task_id"] == "mech-normalize-holdout-01")
    other_scopes = [row["route_scope"] for row in all_tasks if row["partition"] == "holdout" and row["task_id"] != "mech-normalize-holdout-01"]
    assert packet["task"]["arm"] == "B0"
    assert packet["task"]["route_scope"] == own_task["route_scope"]
    assert own_task["route_scope"] in packet_text
    assert not any(scope in packet_text for scope in other_scopes)
    assert packet["advisory_payload"] is None
    assert "allowed_advice_codes" not in packet_text
    assert "adv-" not in packet_text
    assert "M1" not in packet_text
    assert "S1" not in packet_text
    assert manifest["arm"] == "B0"
    assert manifest["execution_allowed"] is False


def test_preparer_m1_packet_lacks_s1_sham_terms():
    preparer = load_preparer()
    packet, _manifest = preparer.build_packet("mech-normalize-holdout-01", "M1")
    packet_text = json.dumps(packet, sort_keys=True)
    assert packet["advisory_payload"]["arm"] == "M1"
    assert [entry["code"] for entry in packet["advisory_payload"]["entries"]] == [
        "adv-normalize-trim",
        "adv-normalize-canonicalize",
        "adv-normalize-empty-guard",
    ]
    assert "adv-cache-key-scope" not in packet_text
    assert "adv-cache-invalidation" not in packet_text
    assert "adv-cache-fallback" not in packet_text
    assert "S1" not in packet_text
    assert "allowed_advice_codes" not in packet_text


def test_preparer_s1_packet_lacks_m1_relevant_advice_codes():
    preparer = load_preparer()
    packet, _manifest = preparer.build_packet("mech-normalize-holdout-01", "S1")
    packet_text = json.dumps(packet, sort_keys=True)
    assert packet["advisory_payload"]["arm"] == "S1"
    assert [entry["code"] for entry in packet["advisory_payload"]["entries"]] == [
        "adv-cache-key-scope",
        "adv-cache-invalidation",
        "adv-cache-fallback",
    ]
    assert "adv-normalize-trim" not in packet_text
    assert "adv-normalize-canonicalize" not in packet_text
    assert "adv-normalize-empty-guard" not in packet_text
    assert "M1" not in packet_text
    assert "allowed_advice_codes" not in packet_text


def test_preparer_rejects_invalid_task_arm_and_run_path(tmp_path):
    preparer = load_preparer()
    runs_root = tmp_path / "runs"
    for task_id, arm in (("missing-task", "B0"), ("mech-normalize-learn-01", "B0"), ("mech-normalize-holdout-01", "BAD")):
        try:
            preparer.build_packet(task_id, arm)
        except preparer.PrepareError:
            pass
        else:
            raise AssertionError(f"expected PrepareError for {task_id}/{arm}")
    try:
        preparer.prepare_workspace("mech-normalize-holdout-01", "B0", Path("relative-runs"))
    except preparer.PrepareError:
        pass
    else:
        raise AssertionError("expected relative runs_root rejection")
    try:
        preparer.prepare_workspace("mech-normalize-holdout-01", "B0", runs_root, run_id="../escape")
    except preparer.PrepareError:
        pass
    else:
        raise AssertionError("expected unsafe run_id rejection")


def test_preparer_rejects_payload_hash_mismatch(tmp_path, monkeypatch):
    preparer = load_preparer()
    copied_root = tmp_path / "mechanism_v2_copy"
    shutil.copytree(ROOT, copied_root)
    payload_path = copied_root / "private" / "advisory_payloads.jsonl"
    payload_path.write_text(payload_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    monkeypatch.setattr(preparer, "ROOT", copied_root)
    try:
        preparer.build_packet("mech-normalize-holdout-01", "M1")
    except preparer.PrepareError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("expected payload hash mismatch rejection")


def test_preparation_creates_only_allowed_files_and_modes(tmp_path):
    preparer = load_preparer()
    workspace = preparer.prepare_workspace("mech-normalize-holdout-01", "S1", tmp_path / "runs")
    assert {path.name for path in workspace.iterdir()} == {"agent_packet.json", "run_manifest.json", "RECEIPT_SCHEMA.json"}
    assert workspace.stat().st_mode & 0o777 == 0o700
    for path in workspace.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
    packet = json.loads((workspace / "agent_packet.json").read_text(encoding="utf-8"))
    manifest = json.loads((workspace / "run_manifest.json").read_text(encoding="utf-8"))
    assert packet["preparation_boundary"]["execution_allowed"] is False
    assert isinstance(packet["task"].get("route_scope"), str)
    assert "out of scope" in packet["task"]["route_scope"].lower()
    assert manifest["agent_packet"] == "agent_packet.json"
    assert manifest["run_identity"] == "mech-normalize-holdout-01--S1"
    workspace_text = "\n".join(path.read_text(encoding="utf-8") for path in workspace.iterdir())
    assert "private_expected_check_codes" not in workspace_text
    assert "quality_label_placeholder" not in workspace_text
    assert "counterbalanced_schedule" not in workspace_text
