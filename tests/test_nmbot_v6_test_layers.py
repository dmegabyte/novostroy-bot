import importlib.util
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "nmbot_v6_test_layers.py"
SPEC = importlib.util.spec_from_file_location("v6_layers", SCRIPT)
layers = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(layers)
ASSERTIONS_SCRIPT = ROOT / "eval" / "nmbot-v6-layered" / "assertions.py"
ASSERTIONS_SPEC = importlib.util.spec_from_file_location("v6_layer_assertions", ASSERTIONS_SCRIPT)
layer_assertions = importlib.util.module_from_spec(ASSERTIONS_SPEC)
assert ASSERTIONS_SPEC.loader is not None
ASSERTIONS_SPEC.loader.exec_module(layer_assertions)


def test_inventory_has_explicit_proof_boundaries(capsys):
    assert layers.main(["--list"]) == 0
    output = capsys.readouterr().out
    assert '"prompt1"' in output and '"does_not_prove"' in output
    assert '"vps_jivo": true' in output


def test_dry_run_never_calls_subprocess(capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("subprocess must not run")

    assert layers.main(["--layer", "runtime"], run=forbidden) == 0
    assert "dry_run" in capsys.readouterr().out


def test_model_requires_confirmation(capsys):
    assert layers.main(["--layer", "prompt1", "--execute"], run=lambda *a, **k: None) == 2
    assert "--confirm-model" in capsys.readouterr().err


def test_live_requires_confirmation(capsys):
    assert layers.main(["--layer", "contour", "--execute"], run=lambda *a, **k: None) == 2
    assert "--confirm-live" in capsys.readouterr().err


def test_missing_key_fails_closed_without_value(tmp_path, capsys):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OTHER_KEY=not-for-output\n", encoding="utf-8")
    assert layers.main(["--layer", "prompt1", "--execute", "--confirm-model", "--env-file", str(dotenv)], environ={}, run=lambda *a, **k: None) == 2
    captured = capsys.readouterr()
    assert "OPENROUTER_API_KEY" in captured.err
    assert "not-for-output" not in captured.out + captured.err


def test_command_construction_and_return_code(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    assert layers.main(["--layer", "runtime", "--execute"], environ={}, run=fake_run) == 7
    command, kwargs = calls[0]
    assert command[:4] == [layers.sys.executable, "-m", "pytest", "-q"]
    assert "tests/test_nmbot_v6_finance_prompt.py" in command
    assert kwargs["shell"] is False and kwargs["cwd"] == ROOT


def test_prompt_command_loads_key_and_uses_expected_config(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("OPENROUTER_API_KEY=secret\n", encoding="utf-8")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    assert layers.main(["--layer", "prompt2", "--execute", "--confirm-model", "--env-file", str(dotenv)], environ={}, run=fake_run) == 0
    command, kwargs = calls[0]
    assert command == ["promptfoo", "eval", "-c", "eval/nmbot-v6-layered/prompt2-contextual.yaml", "--no-cache", "--max-concurrency", "1"]
    assert kwargs["shell"] is False and kwargs["env"]["OPENROUTER_API_KEY"] == "secret"


def test_all_layer_commands_reference_existing_project_paths():
    for layer_name in ("prompt1", "prompt2", "runtime", "contour"):
        command = layers.build_command(layer_name)
        for argument in command:
            if argument.startswith(("eval/", "tests/", "scripts/")):
                assert (ROOT / argument).exists(), f"missing {layer_name} path: {argument}"


def test_prompt2_assertion_rejects_alternative_question_slots():
    output = '{"action":"reply","response":"Ответ.","final_question":"Площадь или бюджет?"}'
    result = layer_assertions.get_assert(output, {"vars": {"layer": "prompt2"}})
    assert result["pass"] is False
    assert "alternative" in result["reason"]
