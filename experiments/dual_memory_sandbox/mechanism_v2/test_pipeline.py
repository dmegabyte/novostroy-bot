from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from multiprocessing import Process, Queue
from pathlib import Path

import aggregate_compare
import blind_route_scorer
import metrics_collector_v2
import orchestration_contract
import prepare_run
import route_safety
import seal_result


ROOT = Path(__file__).resolve().parent


def write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class MechanismV2PipelineTests(unittest.TestCase):
    def prepare(self, tmp: Path, task_id: str = "mech-normalize-holdout-01", arm: str = "M1") -> Path:
        return prepare_run.prepare_workspace(task_id, arm, tmp)

    def candidate(self, task_id: str = "mech-normalize-holdout-01", arm: str = "M1", checks: list[str] | None = None) -> dict:
        if checks is None:
            checks = ["check-normalize-input", "check-normalize-empty"]
        allow = json.loads((ROOT / "experiment.json").read_text())["receipt_advice_allowlist"][task_id][arm]
        return {
            "task_id": task_id,
            "arm": arm,
            "selected_check_codes": checks,
            "route_summary": "Choose the public validation route and verify the relevant safe checks.",
            "receipt": {
                "task_id": task_id,
                "arm": arm,
                "consulted_advice_codes": allow,
                "selected_check_codes": checks,
                "receipt_version": "mechanism-v2-receipt-1",
            },
        }

    def test_output_schema_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            cand = self.candidate()
            cand["edits"] = []
            path = Path(td) / "candidate.json"
            write_json(path, cand)
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "ses_schemaReject")

    def test_arm_leak_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            for idx, summary in enumerate(["M1 used advice for this route.", "Used (M1) advice.", "route arm=M1", "Pick [S1] route.", "b0 route", "B 0 was selected.", "Use M-1 guidance.", "Prefer s_1 checks.", "Selected (m 1).", "arm = B0"]):
                cand = self.candidate()
                cand["route_summary"] = summary
                path = Path(td) / f"candidate-{idx}.json"
                write_json(path, cand)
                with self.assertRaises(seal_result.SealError):
                    seal_result.seal_candidate(ws, path, f"ses_armLeak{idx}")

    def test_shared_arm_leak_detector_keeps_safe_prose(self):
        leaks = ["B 0", "M-1", "s_1", "(m 1)", "arm = B0"]
        for summary in leaks:
            self.assertTrue(route_safety.route_summary_leaks_arm_identity(summary), summary)
        safe = [
            "Choose the public validation route and verify the relevant safe checks.",
            "Baseline quality is discussed without naming a route identity.",
            "The problem has one boundary and zero private payload details.",
            "Symbols and prose stay natural without arm identifiers.",
        ]
        for summary in safe:
            self.assertFalse(route_safety.route_summary_leaks_arm_identity(summary), summary)

    def test_receipt_mismatch_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            cand = self.candidate()
            cand["receipt"]["selected_check_codes"] = ["check-normalize-idempotent"]
            path = Path(td) / "candidate.json"
            write_json(path, cand)
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "ses_receiptMismatch")

    def test_blind_scorer_arm_independence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ws_a = self.prepare(root / "a", arm="M1")
            ws_b = self.prepare(root / "b", arm="S1")
            ca = root / "a.json"
            cb = root / "b.json"
            write_json(ca, self.candidate(arm="M1"))
            write_json(cb, self.candidate(arm="S1"))
            seal_result.seal_candidate(ws_a, ca, "ses_blindA")
            seal_result.seal_candidate(ws_b, cb, "ses_blindB")
            sa = blind_route_scorer.score_sealed(ws_a / "sealed_result.json")
            sb = blind_route_scorer.score_sealed(ws_b / "sealed_result.json")
            self.assertEqual(sa["quality_pass"], sb["quality_pass"])
            self.assertTrue(sa["scorer_blind_to_arm"])
            self.assertEqual(sa["private_labels_sha256"], seal_result._sha256(ROOT / "private" / "labels.jsonl"))

    def test_sealing_containment_and_hash_binding(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            path = Path(td) / "candidate.json"
            write_json(path, self.candidate())
            sealed = seal_result.seal_candidate(ws, path, "ses_hashBinding")
            self.assertTrue((ws / "sealed_result.json").is_file())
            self.assertEqual(sealed["source_hashes"], json.loads((ws / "run_manifest.json").read_text())["source_hashes"])
            self.assertIn("private/labels.jsonl", sealed["source_hashes"])
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "ses_hashBindingAgain")

    def test_atomic_write_rejects_existing_and_allows_one_link_winner(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "sealed_result.json"
            write_json(target, {"winner": "existing"})
            with self.assertRaises(seal_result.SealError):
                seal_result._atomic_write_new_json(target, {"winner": "late"})
            self.assertEqual(json.loads(target.read_text()), {"winner": "existing"})
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "sealed_result.json"
            q: Queue = Queue()

            def worker(idx: int) -> None:
                try:
                    seal_result._atomic_write_new_json(target, {"winner": idx})
                    q.put((idx, True))
                except Exception:
                    q.put((idx, False))

            procs = [Process(target=worker, args=(idx,)) for idx in range(8)]
            for proc in procs:
                proc.start()
            for proc in procs:
                proc.join(10)
                self.assertEqual(proc.exitcode, 0)
            outcomes = [q.get(timeout=1) for _ in procs]
            self.assertEqual(sum(1 for _, ok in outcomes if ok), 1)
            winner = json.loads(target.read_text())["winner"]
            self.assertIn((winner, True), outcomes)
            self.assertFalse(list(Path(td).glob(".sealed_result.json.tmp-*")))

    def test_seal_rejects_bad_session_id_and_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            path = Path(td) / "candidate.json"
            write_json(path, self.candidate())
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "")
            seal_result.seal_candidate(ws, path, "ses_noOverwrite")
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "ses_noOverwrite2")

    def test_sealing_hash_binding_rejects_mutated_manifest_before_write(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            path = Path(td) / "candidate.json"
            write_json(path, self.candidate())
            manifest = json.loads((ws / "run_manifest.json").read_text())
            manifest["source_hashes"]["experiment.json"] = "bad"
            write_json(ws / "run_manifest.json", manifest)
            manifest = json.loads((ws / "run_manifest.json").read_text())
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "ses_hashBinding2")
            self.assertFalse((ws / "sealed_result.json").exists())

    def test_sealing_payload_provenance_rejects_payload_or_run_manifest_drift(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td) / "run_manifest_drift")
            path = Path(td) / "candidate-run-manifest-drift.json"
            write_json(path, self.candidate())
            manifest = json.loads((ws / "run_manifest.json").read_text())
            manifest["source_hashes"]["private/advisory_payloads.jsonl"] = "0" * 64
            write_json(ws / "run_manifest.json", manifest)
            with self.assertRaises(seal_result.SealError):
                seal_result.seal_candidate(ws, path, "ses_payloadRunManifestDrift")
            self.assertFalse((ws / "sealed_result.json").exists())

        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td) / "payload_artifact_drift")
            path = Path(td) / "candidate-payload-artifact-drift.json"
            write_json(path, self.candidate())
            original_sha256 = seal_result._sha256

            def drifted_sha256(file_path: Path) -> str:
                if file_path == ROOT / "private" / "advisory_payloads.jsonl":
                    return "f" * 64
                return original_sha256(file_path)

            try:
                seal_result._sha256 = drifted_sha256
                with self.assertRaises(seal_result.SealError):
                    seal_result.seal_candidate(ws, path, "ses_payloadArtifactDrift")
            finally:
                seal_result._sha256 = original_sha256
            self.assertFalse((ws / "sealed_result.json").exists())

    def full_cohort(self, tmp: Path, m1_quality: bool = True) -> tuple[Path, Path]:
        exp = json.loads((ROOT / "experiment.json").read_text())
        sessions = []
        for task_id in exp["holdout_task_ids"]:
            family_checks = json.loads((ROOT / "public" / "tasks.jsonl").read_text().splitlines()[exp["holdout_task_ids"].index(task_id) + 9])["allowed_check_codes"]
            label_checks = {row["task_id"]: row["private_expected_check_codes"] for row in [json.loads(line) for line in (ROOT / "private" / "labels.jsonl").read_text().splitlines()]}[task_id]
            for arm in ["B0", "M1", "S1"]:
                ws = prepare_run.prepare_workspace(task_id, arm, tmp)
                checks = label_checks if (arm == "M1" and m1_quality) else [family_checks[-1]]
                cand = self.candidate(task_id, arm, checks)
                cand["route_summary"] = "Choose the public validation route and verify safe checks."
                cpath = tmp / f"{task_id}-{arm}.json"
                write_json(cpath, cand)
                sid = f"ses_{task_id.replace('-', '_')}_{arm}"
                seal_result.seal_candidate(ws, cpath, sid)
                blind_route_scorer.write_score(ws / "sealed_result.json")
                sessions.append({
                    "schema_version": 1,
                    "source": "opencode_db_normalized_aggregates",
                    "task_id": task_id,
                    "mode": "memory" if arm == "M1" else ("sham" if arm == "S1" else "baseline"),
                    "phase": arm,
                    "session_id": sid,
                    "parent_id": "ses_commonParent",
                    "fresh_subagent_session": True,
                    "actual_agent": "task",
                    "actual_model_identity": "{\"model_id\":\"same\",\"provider_id\":\"same\",\"variant\":\"same\"}",
                    "diagnostics": {"time_created_ms": 1, "time_updated_ms": 2},
                    "resources": {"wall_ms": 10 if arm == "M1" else 20, "input_tokens": 40, "output_tokens": 50, "reasoning_tokens": 10, "tokens_cache_read": 0, "tokens_cache_write": 0, "cached_tokens": 0, "total_tokens": 100, "estimated_provider_cost": 0.01, "model_calls": 1, "tool_calls": 1, "failed_tool_calls": 0, "retries": 0},
                    "coverage": {"status": "complete", "present": ["wall", "tokens", "cost", "model_calls", "tools", "retries"], "missing": []},
                })
        metrics = tmp / "metrics.json"
        write_json(metrics, sessions)
        return tmp, metrics

    def test_aggregate_rejects_mixed_parent_model_duplicate_missing_arm(self):
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td))
            rows = json.loads(metrics.read_text())
            rows[0]["parent_id"] = "ses_otherParent"
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
            rows[0]["parent_id"] = "ses_commonParent"
            rows[0]["actual_model_identity"] = "{\"model_id\":\"other\",\"provider_id\":\"same\",\"variant\":\"same\"}"
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
            rows[0]["actual_model_identity"] = "{\"model_id\":\"same\",\"provider_id\":\"same\",\"variant\":\"same\"}"
            rows[0]["parent_id"] = ""
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
            rows[0]["parent_id"] = "ses_commonParent"
            rows[0]["actual_agent"] = ""
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
            rows[0]["actual_agent"] = "task"
            rows[1]["session_id"] = rows[0]["session_id"]
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td))
            missing = next(Path(td).glob("mech-normalize-holdout-01--B0/sealed_result.json"))
            missing.unlink()
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)

    def test_aggregate_rejects_wrong_score_binding_schema_and_missing_resources(self):
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td))
            score_path = next(Path(td).glob("mech-normalize-holdout-01--B0/blind_score.json"))
            score = json.loads(score_path.read_text())
            score["sealed_result_sha256"] = "bad"
            write_json(score_path, score)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td))
            score_path = next(Path(td).glob("mech-normalize-holdout-01--B0/blind_score.json"))
            score = json.loads(score_path.read_text())
            score["private_labels_sha256"] = "0" * 64
            write_json(score_path, score)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td))
            rows = json.loads(metrics.read_text())
            del rows[0]["resources"]["total_tokens"]
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)
            rows = json.loads(metrics.read_text())
            rows[0]["resources"]["total_tokens"] = 100
            rows[0]["coverage"] = {"status": "not_evaluable", "present": ["wall"], "missing": ["tokens"]}
            write_json(metrics, rows)
            with self.assertRaises(aggregate_compare.AggregateError):
                aggregate_compare.aggregate(runs, metrics)

    def test_mechanism_result_only_when_m1_beats_both_controls(self):
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td), m1_quality=True)
            result = aggregate_compare.aggregate(runs, metrics)
            self.assertEqual(result["mechanism_condition"], "mechanism_evidence_observed")
            self.assertEqual(result["claim"], "observational_only_no_causal_claim")
        with tempfile.TemporaryDirectory() as td:
            runs, metrics = self.full_cohort(Path(td), m1_quality=False)
            result = aggregate_compare.aggregate(runs, metrics)
            self.assertEqual(result["mechanism_condition"], "mechanism_not_evaluable")

    def test_temp_sqlite_metrics_collector_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "opencode.sqlite"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE session (id TEXT, parent_id TEXT, agent TEXT, model TEXT, cost REAL, tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER, tokens_cache_read INTEGER, tokens_cache_write INTEGER, time_created INTEGER, time_updated INTEGER)")
            conn.execute("CREATE TABLE part (session_id TEXT, data TEXT)")
            model = json.dumps({"providerID": "provider", "modelID": "model", "variant": "variant"})
            conn.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", ("ses_metricOne", "ses_commonParent", "task", model, 0.25, 10, 20, 3, 1, 2, 100, 160))
            conn.execute("INSERT INTO part VALUES (?, ?)", ("ses_metricOne", json.dumps({"type": "tool", "state": {"status": "completed"}})))
            conn.execute("INSERT INTO part VALUES (?, ?)", ("ses_metricOne", json.dumps({"type": "tool", "state": {"status": "error"}})))
            conn.execute("INSERT INTO part VALUES (?, ?)", ("ses_metricOne", json.dumps({"type": "step-finish"})))
            conn.commit()
            conn.close()
            metric = metrics_collector_v2.collect_metrics(db_path=db, session_id="ses_metricOne", task_id="mech-normalize-holdout-01", arm="M1", expected_parent_id="ses_commonParent")
            self.assertEqual(metric["resources"]["wall_ms"], 60)
            self.assertEqual(metric["resources"]["total_tokens"], 33)
            self.assertEqual(metric["resources"]["tool_calls"], 2)
            self.assertEqual(metric["resources"]["failed_tool_calls"], 1)
            self.assertEqual(metric["coverage"]["status"], "complete")
            with self.assertRaises(metrics_collector_v2.MetricsV2Error):
                metrics_collector_v2.collect_metrics(db_path=db, session_id="ses_metricOne", task_id="mech-normalize-holdout-01", arm="M1", expected_parent_id="ses_wrongParent")

    def test_orchestrator_contract_packet_only_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            ws = self.prepare(Path(td))
            result = orchestration_contract.validate_workspace_packet(ws)
            self.assertEqual(result["status"], "valid_contract_only")
            packet = json.loads((ws / "agent_packet.json").read_text())
            prompt = orchestration_contract.render_subagent_prompt(packet)
            self.assertIn("AGENT_PACKET_JSON_START", prompt)
            self.assertIn(json.dumps(packet, ensure_ascii=False, sort_keys=True), prompt)
            outside = prompt.replace(json.dumps(packet, ensure_ascii=False, sort_keys=True), "")
            self.assertNotIn("private_labels", outside)
            self.assertNotIn("other_arm_payloads", outside)
            self.assertNotIn("own_outcome", outside)


if __name__ == "__main__":
    unittest.main()
