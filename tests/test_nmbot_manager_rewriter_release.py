import ast
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import scripts.nmbot_manager_rewriter_release as rel


ROOT = Path(__file__).resolve().parents[1]
ARCHIVED_BUNDLE = ROOT / "docs" / "archive" / "release-candidates" / "2026-07-24" / "manager_rewriter"
SOURCE_PROMPT = ROOT / "prompts" / "v2_manager_rewriter.txt"
BUNDLE_PROMPT = ARCHIVED_BUNDLE / "candidates" / "prompts" / "v2_manager_rewriter.txt"
SOURCE_IDENTITY = ROOT / "data" / "nmbot_release_identity.json"
CANDIDATE_ADAPTER = ARCHIVED_BUNDLE / "candidates" / "scripts" / "nmbot_runtime_adapter.py"
CANDIDATE_RUNTIME = ARCHIVED_BUNDLE / "candidates" / "nmbot_v2" / "runtime.py"
CANDIDATE_PENDING = ARCHIVED_BUNDLE / "candidates" / "nmbot_v2" / "pending.py"
CANDIDATE_RESPONSE = ARCHIVED_BUNDLE / "candidates" / "nmbot_v2" / "response.py"
CANDIDATE_IDENTITY = ARCHIVED_BUNDLE / "candidates" / "data" / "nmbot_release_identity.json"
EXPECTED_CANDIDATE_RELEASE_ID = "2026-07-24.v2v3-phone-first-callback.12"


def test_legacy_bundle_path_is_a_redirect_to_the_archived_owner():
    legacy_bundle = ROOT / "release_bundles" / "manager_rewriter"
    redirect = legacy_bundle / "README.md"
    assert redirect.is_file()
    assert [path.name for path in legacy_bundle.iterdir()] == ["README.md"]
    assert "docs/archive/release-candidates/2026-07-24/manager_rewriter/" in redirect.read_text(encoding="utf-8")
    assert (ARCHIVED_BUNDLE / "manifest.json").is_file()


def _run_candidate_overlay(code: str) -> subprocess.CompletedProcess[str]:
    manifest = rel.load_manifest()
    targets = rel.targets_from_manifest(manifest)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        shutil.copytree(ROOT / "nmbot_v2", tmp_root / "nmbot_v2")
        shutil.copytree(ROOT / "nmbot_v0", tmp_root / "nmbot_v0")
        shutil.copytree(ROOT / "scripts", tmp_root / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(ROOT / "followup_intent_classifier.py", tmp_root / "followup_intent_classifier.py")
        for target in targets:
            src = rel.DEFAULT_CANDIDATES / target.path
            dst = tmp_root / target.path
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
        env = dict(os.environ)
        env["PYTHONPATH"] = str(tmp_root) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.run([sys.executable, "-c", code], cwd=tmp_root, env=env, text=True, capture_output=True, check=False)


class FakeRemote:
    def __init__(self, *, drift=False, fail_compile=False, secret_error=False, baseline_health=None, post_health=None, rollback_health=None):
        self.drift = drift
        self.fail_compile = fail_compile
        self.secret_error = secret_error
        self.baseline_health = {"ok": True} if baseline_health is None else baseline_health
        self.post_health = {"ok": True} if post_health is None else post_health
        self.rollback_health = {"ok": True} if rollback_health is None else rollback_health
        self.commands = []
        self.uploads = []
        self.backup_dir = "backups/manager-rewriter-test"
        self.rolled_back = False

    def run(self, command: str, *, input_text: str | None = None):
        self.commands.append(command)
        manifest = rel.load_manifest()
        targets = rel.targets_from_manifest(manifest)
        if "hashes" in command and "urllib.request" in command:
            hashes = {t.path: ("0" * 64 if self.drift else t.expected_remote_base_sha256) for t in targets}
            health = self.baseline_health
            if self.uploads:
                hashes = {t.path: t.candidate_sha256 for t in targets}
                health = self.post_health
            if self.rolled_back:
                hashes = {t.path: t.expected_remote_base_sha256 for t in targets}
                health = self.rollback_health
            payload = {"hashes": hashes, "runtime_modes": manifest["runtime_modes"], "service_active": "active", "health": health}
            return subprocess.CompletedProcess([], 0, stdout=rel.json.dumps(payload), stderr="")
        if "backups/manager-rewriter" in command:
            return subprocess.CompletedProcess([], 0, stdout=self.backup_dir + "\n", stderr="")
        if command.startswith("mkdir -p"):
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        if "python3 -m py_compile" in command:
            if self.fail_compile:
                err = "OPENROUTER_API_KEY=sk-secret-value"
                return subprocess.CompletedProcess([], 1, stdout="", stderr=err if self.secret_error else "compile failed")
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        if "python3 -c" in command and "import nmbot_v2.manager_rewriter" in command:
            return subprocess.CompletedProcess([], 0, stdout="import=ok\n", stderr="")
        if "scripts/nmbot_manager_rewriter.py" in command and "--runtime" in command:
            return subprocess.CompletedProcess([], 0, stdout="OK runtime mode set\n", stderr="")
        if command == "systemctl --user restart novostroy-bot-api.service":
            return subprocess.CompletedProcess([], 0, stdout="", stderr="")
        if "rollback=ok" in command or "release_identity.json" in command:
            if "rollback=ok" in command:
                self.rolled_back = True
            return subprocess.CompletedProcess([], 0, stdout="rollback=ok\n", stderr="")
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    def upload(self, local: Path, remote_path: str):
        self.uploads.append((local, remote_path))
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")


def test_preflight_is_default_and_local_no_network_words():
    proc = subprocess.run([sys.executable, str(ROOT / "scripts" / "nmbot_manager_rewriter_release.py")], cwd=ROOT, check=True, text=True, capture_output=True)
    assert "local_only_preflight=true" in proc.stdout
    assert "restart=novostroy-bot-api.service only" in proc.stdout


def test_manager_rewriter_source_and_bundle_prompt_are_identical():
    assert SOURCE_PROMPT.read_text(encoding="utf-8") == BUNDLE_PROMPT.read_text(encoding="utf-8")


def test_manager_rewriter_prompt_contract_contains_next_reply_and_raw_material_rules():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "написать следующую реплику менеджера" in prompt
    assert "Не редактируй подготовленный ответ построчно" in prompt
    assert "не считай его обязательным шаблоном" in prompt
    assert "Это сырьё" in prompt
    assert "можно свободно менять порядок и формулировки" in prompt
    assert "полностью убирать всё, что больше не нужно клиенту" in prompt


def test_manager_rewriter_prompt_contract_contains_contextual_followup_and_stale_cta_rules():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "последняя реплика именно в контексте разговора" in prompt
    assert "«да», «нет», «по всем», «просто позвони», «без имени», «второй», «а за метр?»" in prompt
    assert "обычно продолжают предыдущую тему" in prompt
    assert "не повторяй уже заданный вопрос" in prompt
    assert "прежний вопрос или CTA уже закрыт ответом клиента, выброси его" in prompt
    assert "Не повторяй список, цены и объяснения без необходимости" in prompt


def test_manager_rewriter_prompt_contract_contains_fidelity_and_metric_rules():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "Фактическая точность важнее естественности" in prompt
    assert "Используй только факты из входа" in prompt
    assert "Не добавляй новые ЖК, цены, сроки, числа, характеристики, обещания или выводы" in prompt
    assert "Не усиливай расплывчатый факт" in prompt
    assert "не превращай неопределённость в уверенность" in prompt
    assert "цена квартиры, цена за квадратный метр, платёж, первоначальный взнос и бюджет — не взаимозаменяемы" in prompt
    assert "Если запрошенного показателя нет" in prompt
    assert "не подставляй вместо него другое число" in prompt


def test_manager_rewriter_prompt_contract_contains_concise_human_style_rules():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "короткая реплика клиента обычно требует одной-двух коротких фраз" in prompt
    assert "не начинай с «по вопросу ... отвечаю», «по текущим данным», «без нового поиска»" in prompt
    assert "не пересказывай историю разговора" in prompt
    assert "не используй канцелярит" in prompt
    assert "вопрос в конце не обязателен" in prompt
    assert "Задавай не больше одного вопроса" in prompt


def test_manager_rewriter_prompt_contract_contains_business_action_limits():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "честные ограничения" in prompt
    assert "допустимое бизнес-действие" in prompt
    assert "Сохраняй только ещё актуальные ограничения и действия" in prompt
    assert "Не обещай звонок, проверку, бронь или передачу оператору" in prompt


def test_manager_rewriter_prompt_contract_contains_answer_first_operator_second_policy():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "Как выбирать ход" in prompt
    assert "Если подтверждённые факты полностью отвечают" in prompt
    assert "Не веди к оператору и не проси телефон, когда ответ уже есть" in prompt
    assert "Если нужного факта нет или он не подтверждён" in prompt
    assert "разрешает передачу оператору" in prompt
    assert "скажи границу одной естественной фразой и веди к контакту" in prompt


def test_manager_rewriter_prompt_contract_contains_phone_funnel_branches():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "Если телефона ещё нет в диалоге" in prompt
    assert "На какой номер вам удобно позвонить?" in prompt
    assert "Если телефон уже есть в диалоге" in prompt
    assert "не проси его снова и не повторяй номер" in prompt
    assert "Если клиент отказался от звонка или просит не звонить" in prompt
    assert "не повторяй просьбу о телефоне" in prompt
    assert "Короткое согласие вроде «да», «по всем», «просто позвони» должно продвигать текущую воронку" in prompt


def test_manager_rewriter_prompt_contract_contains_list_question_and_bullet_rules():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "Не показывай больше трёх вариантов" in prompt
    assert "`1.`, `2.`, `3.`" in prompt
    assert "Не используй списки со `*` или `-`" in prompt
    assert "Для короткого подтверждения список не нужен" in prompt


def test_manager_rewriter_prompt_contract_contains_few_shot_anchors():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "Пример 1" in prompt
    assert "Второй ЖК планируют сдать" in prompt
    assert "Пример 2" in prompt
    assert "Хорошо, уточним цену за квадратный метр по всем трём ЖК. На какой номер вам удобно позвонить?" in prompt
    assert "Пример 3" in prompt
    assert "Хорошо, передам оператору запрос по всем трём ЖК." in prompt
    assert "Пример 4" in prompt
    assert "Хорошо, без имени. Передам номер оператору и попрошу перезвонить после 17:00." in prompt
    assert "Пример 5" in prompt
    assert "Хорошо, звонить не будем" in prompt
    assert "Пример 6" in prompt
    assert "Клиент: «второй»" in prompt
    assert "сразу ответить по второму варианту" in prompt


def test_manager_rewriter_prompt_contract_forbids_operator_number_exposure_and_phone_parsing():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    assert "Не выводи и не придумывай внутренние или внешние номера операторов" in prompt
    assert "Не разбирай, не нормализуй и не проверяй телефон клиента" in prompt
    assert "это делает код" in prompt
    assert "не отправляй клиента к застройщику, на сайт или в офис" in prompt


def test_manager_rewriter_good_examples_do_not_expose_input_meta_mechanics():
    prompt = SOURCE_PROMPT.read_text(encoding="utf-8")
    good_lines = [line for line in prompt.splitlines() if line.startswith("Хорошо:")]
    joined = "\n".join(good_lines)
    assert "если это действие доступно во входе" not in joined
    assert "если доступно во входе" not in joined
    assert "во входе" not in joined
    assert "prepared" not in joined.lower()
    assert "payload" not in joined.lower()
    assert "evidence" not in joined.lower()


def test_manager_rewriter_manifest_prompt_sha_matches_candidate_file():
    manifest = rel.load_manifest()
    target = next(item for item in manifest["targets"] if item["path"] == "prompts/v2_manager_rewriter.txt")
    assert target["candidate_sha256"] == rel.sha256_file(BUNDLE_PROMPT)


def test_manager_rewriter_release_identity_is_real_and_populated():
    identity = json.loads(SOURCE_IDENTITY.read_text(encoding="utf-8"))
    assert identity["release_id"] == "local-unreleased"
    assert identity["tracked_files"]


def test_candidate_adapter_wires_manager_rewriter_to_turn_processor():
    tree = ast.parse(CANDIDATE_ADAPTER.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run_v2_authoritative")
    calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call)]
    assert any(isinstance(call.func, ast.Name) and call.func.id == "_runtime_response_composer_mode" for call in calls)
    assert any(isinstance(call.func, ast.Name) and call.func.id == "_runtime_manager_rewriter_mode" for call in calls)
    processor_call = next(call for call in calls if isinstance(call.func, ast.Name) and call.func.id == "TurnProcessor")
    kwargs = {kw.arg for kw in processor_call.keywords}
    assert "manager_rewriter" in kwargs
    assert "manager_rewriter_mode" in kwargs


def test_candidate_runtime_defines_manager_transcript_and_finalize_dialogue_turn_static():
    tree = ast.parse(CANDIDATE_RUNTIME.read_text(encoding="utf-8"))
    assert any(isinstance(node, ast.FunctionDef) and node.name == "_manager_rewriter_transcript" for node in tree.body)
    finalize = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_finalize_delta"
    )
    replace_call = next(node for node in ast.walk(finalize) if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "replace")
    assert "append_dialogue_turn" in {kw.arg for kw in replace_call.keywords}


def test_candidate_manager_rewriter_adapter_boundary_uses_manager_timeout_and_payload_stage():
    code = r'''
import asyncio
import hashlib
import inspect
import json
from pathlib import Path

import scripts.nmbot_runtime_adapter as adapter

class FakeGateway:
    def __init__(self):
        self.calls = []
    async def _run_gateway_request_once(self, request_data, headers, timeout):
        self.calls.append({"request_data": request_data, "headers": headers, "timeout": timeout})
        return {"text": "Живой ответ менеджера."}, {"ok": True, "status": "ok"}

async def main():
    fake = FakeGateway()
    app = {"overmind_client": fake}
    rewriter = adapter._ManagerRewriterAdapter(app)
    result = await rewriter.rewrite_manager_answer(
        transcript=({"user": "найди", "assistant": ""},),
        current_question="найди",
        prepared_answer="Подготовленный ответ.",
        brief=None,
    )
    sig = inspect.signature(adapter._run_v2_response_gateway_once)
    timeout_param = sig.parameters["timeout_env"]
    module_file = Path(adapter.__file__).resolve()
    print(json.dumps({
        "adapter_file": str(module_file),
        "adapter_sha256": hashlib.sha256(module_file.read_bytes()).hexdigest(),
        "call_count": len(fake.calls),
        "timeout": fake.calls[0]["timeout"],
        "payload_stage": fake.calls[0]["request_data"].get("_payload_stage"),
        "text": result,
        "timeout_env_kind": str(timeout_param.kind),
        "timeout_env_default": timeout_param.default,
    }, ensure_ascii=False))

asyncio.run(main())
'''
    env_code = "import os; os.environ['NMBOT_MANAGER_REWRITER_TIMEOUT']='7'; " + code
    proc = _run_candidate_overlay(env_code)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    manifest = rel.load_manifest()
    adapter_target = next(item for item in manifest["targets"] if item["path"] == "scripts/nmbot_runtime_adapter.py")
    assert payload["adapter_file"].endswith("/scripts/nmbot_runtime_adapter.py")
    assert payload["adapter_sha256"] == adapter_target["candidate_sha256"]
    assert payload["timeout_env_kind"] == "KEYWORD_ONLY"
    assert payload["timeout_env_default"] == "NMBOT_V2_RESPONSE_TIMEOUT"
    assert payload["call_count"] == 1
    assert payload["timeout"] == 7
    assert payload["payload_stage"] == "conversation_answer_manager_rewriter"
    assert payload["text"].strip()


def test_candidate_runtime_overlay_hashes_match_manifest_candidate_hashes():
    code = r'''
import hashlib
import json
from pathlib import Path
import nmbot_v2.runtime as runtime
import nmbot_v2.pending as pending
import nmbot_v2.response as response
import scripts.nmbot_runtime_adapter as adapter

runtime_file = Path(runtime.__file__).resolve()
pending_file = Path(pending.__file__).resolve()
response_file = Path(response.__file__).resolve()
adapter_file = Path(adapter.__file__).resolve()
print(json.dumps({
    "runtime_file": str(runtime_file),
    "runtime_sha256": hashlib.sha256(runtime_file.read_bytes()).hexdigest(),
    "pending_file": str(pending_file),
    "pending_sha256": hashlib.sha256(pending_file.read_bytes()).hexdigest(),
    "response_file": str(response_file),
    "response_sha256": hashlib.sha256(response_file.read_bytes()).hexdigest(),
    "adapter_file": str(adapter_file),
    "adapter_sha256": hashlib.sha256(adapter_file.read_bytes()).hexdigest(),
}, ensure_ascii=False))
'''
    proc = _run_candidate_overlay(code)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    manifest = rel.load_manifest()
    targets = {item["path"]: item["candidate_sha256"] for item in manifest["targets"]}
    assert payload["runtime_file"].endswith("/nmbot_v2/runtime.py")
    assert payload["pending_file"].endswith("/nmbot_v2/pending.py")
    assert payload["response_file"].endswith("/nmbot_v2/response.py")
    assert payload["adapter_file"].endswith("/scripts/nmbot_runtime_adapter.py")
    assert payload["runtime_sha256"] == targets["nmbot_v2/runtime.py"]
    assert payload["pending_sha256"] == targets["nmbot_v2/pending.py"]
    assert payload["response_sha256"] == targets["nmbot_v2/response.py"]
    assert payload["adapter_sha256"] == targets["scripts/nmbot_runtime_adapter.py"]


def test_candidate_overlay_phone_first_accept_and_callback_queue():
    code = r'''
import asyncio
import hashlib
import json
from pathlib import Path

from nmbot_v2.contracts import SemanticPlan
import nmbot_v2.pending as pending
import nmbot_v2.response as response
import scripts.nmbot_runtime_adapter as adapter

class Store:
    def __init__(self):
        self.states = {"u": {}}
    async def get(self, user_id):
        return self.states.setdefault(user_id, {})
    async def save(self, user_id, state):
        self.states[user_id] = state

class Outbox:
    def __init__(self):
        self.records = []
    def enqueue_callback(self, **kwargs):
        self.records.append(kwargs)
        class Result:
            lead_ref = "local-ref"
            def public(self):
                return {"status": "queued", "lead_ref": self.lead_ref}
        return Result()

async def fake_plan(self, context, state):
    return SemanticPlan(operation="operator", operator_consent=True, explicit_operator_request=True, query_text=context.user_text)

async def main():
    adapter._SemanticPlannerAdapter.plan = fake_plan
    outbox = Outbox()
    app = {"state_store": Store(), "crm_callback_outbox": outbox, "overmind_client": object()}
    first = await adapter.run_runtime_turn(app, user_id="u", message="позови оператора", channel="jivo", meta={"event_id": "accept"})
    state_after_first = dict(app["state_store"].states["u"]["nmbot_v2"])
    second = await adapter.run_runtime_turn(app, user_id="u", message="+7 999 123-45-67", channel="jivo", meta={"event_id": "phone", "sender_name": "synthetic-nmbot"})
    pending_file = Path(pending.__file__).resolve()
    response_file = Path(response.__file__).resolve()
    adapter_file = Path(adapter.__file__).resolve()
    print(json.dumps({
        "pending_sha256": hashlib.sha256(pending_file.read_bytes()).hexdigest(),
        "response_sha256": hashlib.sha256(response_file.read_bytes()).hexdigest(),
        "adapter_sha256": hashlib.sha256(adapter_file.read_bytes()).hexdigest(),
        "first": first,
        "state_after_first": state_after_first,
        "second": second,
        "records": outbox.records,
    }, ensure_ascii=False))

asyncio.run(main())
'''
    proc = _run_candidate_overlay(code)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    manifest = rel.load_manifest()
    targets = {item["path"]: item["candidate_sha256"] for item in manifest["targets"]}
    assert payload["pending_sha256"] == targets["nmbot_v2/pending.py"]
    assert payload["response_sha256"] == targets["nmbot_v2/response.py"]
    assert payload["adapter_sha256"] == targets["scripts/nmbot_runtime_adapter.py"]
    assert payload["first"]["intent"] == "collect_contact_phone"
    assert payload["first"]["awaiting_phone"] is True
    assert "На какой номер вам удобно позвонить?" in payload["first"]["answer"]
    assert "Как к вам обращаться" not in payload["first"]["answer"]
    assert payload["state_after_first"]["pending_followup"] == "contact_phone"
    assert payload["second"]["intent"] == "callback_queued"
    assert payload["records"][0]["contact_name"] == "Без имени"
    assert payload["records"][0]["normalized_phone"] == "+79991234567"


def test_candidate_runtime_manager_publish_path_executes_and_stores_full_transcript():
    code = r'''
import hashlib
import json
from pathlib import Path
from nmbot_v2.contracts import SafeTurnContext, SearchResult, SemanticPlan
from nmbot_v2.runtime import TurnProcessor, _temporary_strip_repeated_finance_unknown_sentence
from nmbot_v2.state import ConversationState
import scripts.nmbot_runtime_adapter as adapter

class Planner:
    def __init__(self):
        self.calls = 0
    def plan(self, context, state):
        self.calls += 1
        return SemanticPlan(operation="search", intent="life")

class SearchService:
    def search(self, plan, state):
        return SearchResult.from_dict({"facts": [{"name": "Лучи", "price_min": 12000000}]})
    def enrich_selected(self, option, state, plan):
        return option

class FakeGateway:
    def __init__(self):
        self.calls = []
    async def _run_gateway_request_once(self, request_data, headers, timeout):
        self.calls.append({"request_data": request_data, "headers": headers, "timeout": timeout})
        return "Опубликованный ответ менеджера. Что посмотрим дальше?", {"ok": True, "status": "ok"}

gateway = FakeGateway()
rewriter = adapter._ManagerRewriterAdapter({"overmind_client": gateway})
processor = TurnProcessor(planner=Planner(), search_service=SearchService(), manager_rewriter=rewriter, manager_rewriter_mode="publish")
first = processor.process(SafeTurnContext(conversation_ref="local", user_text="найди"))
second = processor.process(SafeTurnContext(conversation_ref="local", user_text="а второй?"), ConversationState.from_dict(first.state))
runtime_file = Path(__import__("nmbot_v2.runtime").runtime.__file__).resolve()
adapter_file = Path(adapter.__file__).resolve()
payload = {
    "runtime_file": str(runtime_file),
    "runtime_sha256": hashlib.sha256(runtime_file.read_bytes()).hexdigest(),
    "adapter_file": str(adapter_file),
    "adapter_sha256": hashlib.sha256(adapter_file.read_bytes()).hexdigest(),
    "first_response": first.response_text,
    "first_trace": first.trace["manager_rewriter"],
    "recent_assistant": first.state["recent_turns"][-1]["assistant"],
    "dialogue_assistant": first.state["dialogue_turns"][-1]["assistant"],
    "gateway_call_count": len(gateway.calls),
    "timeouts": [call["timeout"] for call in gateway.calls],
    "payload_stages": [call["request_data"].get("_payload_stage") for call in gateway.calls],
    "first_transcript": json.loads(gateway.calls[0]["request_data"]["query"].split("=", 1)[1])["full_sanitized_transcript"],
    "second_transcript": json.loads(gateway.calls[1]["request_data"]["query"].split("=", 1)[1])["full_sanitized_transcript"],
    "finance_phrase_regression": _temporary_strip_repeated_finance_unknown_sentence(
        "В ЖК Лучи есть семейные планировки от 12 млн рублей. "
        "К сожалению, у меня нет информации о финансовой стороне этих предложений. "
        "Хотите, передам оператору запрос на уточнение условий?"
    ),
}
print(json.dumps(payload, ensure_ascii=False))
'''
    env_code = "import os; os.environ['NMBOT_MANAGER_REWRITER_TIMEOUT']='11'; " + code
    proc = _run_candidate_overlay(env_code)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    manifest = rel.load_manifest()
    targets = {item["path"]: item["candidate_sha256"] for item in manifest["targets"]}
    assert payload["runtime_file"].endswith("/nmbot_v2/runtime.py")
    assert payload["adapter_file"].endswith("/scripts/nmbot_runtime_adapter.py")
    assert payload["runtime_sha256"] == targets["nmbot_v2/runtime.py"]
    assert payload["adapter_sha256"] == targets["scripts/nmbot_runtime_adapter.py"]
    assert payload["first_response"] == "Опубликованный ответ менеджера. Что посмотрим дальше?"
    assert payload["recent_assistant"] == payload["first_response"]
    assert payload["dialogue_assistant"] == payload["first_response"]
    assert payload["gateway_call_count"] == 2
    assert payload["timeouts"] == [11, 11]
    assert payload["payload_stages"] == ["conversation_answer_manager_rewriter", "conversation_answer_manager_rewriter"]
    assert payload["first_trace"]["used"] is True
    assert payload["first_trace"]["published"] is True
    assert payload["first_trace"]["prompt_provenance"]["prompts"][0]["stage"] == "manager_rewriter"
    assert payload["first_transcript"] == [{"user": "найди", "assistant": ""}]
    assert payload["second_transcript"] == [
        {"user": "найди", "assistant": payload["first_response"]},
        {"user": "а второй?", "assistant": ""},
    ]
    assert "К сожалению, у меня нет информации о финансовой стороне этих предложений." not in payload["finance_phrase_regression"]
    assert "В ЖК Лучи есть семейные планировки от 12 млн рублей." in payload["finance_phrase_regression"]
    assert "Хотите, передам оператору запрос на уточнение условий?" in payload["finance_phrase_regression"]


def test_candidate_identity_matches_manifest_targets_and_hashes():
    manifest = rel.load_manifest()
    targets = rel.targets_from_manifest(manifest)
    identity = json.loads(CANDIDATE_IDENTITY.read_text(encoding="utf-8"))
    assert identity["schema"] == "nmbot.release_identity.v1"
    assert identity["release_id"] == EXPECTED_CANDIDATE_RELEASE_ID
    manifest_hashes = {t.path: t.candidate_sha256 for t in targets}
    tracked = {item["path"]: item["sha256"] for item in identity["tracked_files"]}
    assert set(tracked) == set(manifest_hashes) - {"data/nmbot_release_identity.json"}
    assert tracked == {path: digest for path, digest in manifest_hashes.items() if path != "data/nmbot_release_identity.json"}


def test_deploy_refuses_without_confirm():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "nmbot_manager_rewriter_release.py"), "deploy", "--release-id", "REL-1"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 1
    assert "requires --confirm" in proc.stderr


def test_remote_drift_stops_before_write():
    fake = FakeRemote(drift=True)
    try:
        rel.deploy(release_id=EXPECTED_CANDIDATE_RELEASE_ID, confirm=True, remote=fake)
    except rel.ReleaseError as exc:
        assert "remote drift before write" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy should fail on drift")
    assert fake.uploads == []
    assert not any("backups/manager-rewriter" in command for command in fake.commands)


def test_deploy_order_compile_before_publish_and_only_api_restart():
    fake = FakeRemote()
    out = rel.deploy(release_id=EXPECTED_CANDIDATE_RELEASE_ID, confirm=True, remote=fake)
    joined = "\n".join(fake.commands)
    compile_index = next(i for i, c in enumerate(fake.commands) if "python3 -m py_compile" in c)
    v2_index = next(i for i, c in enumerate(fake.commands) if "nmbot_manager_rewriter.py off --runtime V2" in c)
    v3_index = next(i for i, c in enumerate(fake.commands) if "nmbot_manager_rewriter.py publish --runtime V3" in c)
    assert compile_index < v2_index < v3_index
    assert out.startswith("deploy=ok")
    assert joined.count("systemctl --user restart novostroy-bot-api.service") == 1
    assert "novostroy-bot-n8n-bridge.service" not in joined
    assert "scripts.nmbot_runtime_adapter" in joined


def test_deploy_rejects_mismatched_release_id_before_remote_work():
    fake = FakeRemote()
    try:
        rel.deploy(release_id="2026-07-23.wrong", confirm=True, remote=fake)
    except rel.ReleaseError as exc:
        assert "does not match candidate identity" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy should reject mismatched release_id")
    assert fake.commands == []
    assert fake.uploads == []


def test_strict_health_rejects_missing_ok_before_write_and_after_deploy():
    before = FakeRemote(baseline_health={})
    try:
        rel.deploy(release_id=EXPECTED_CANDIDATE_RELEASE_ID, confirm=True, remote=before)
    except rel.ReleaseError as exc:
        assert "baseline before write" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy should reject missing health ok before write")
    assert before.uploads == []

    after = FakeRemote(post_health={})
    try:
        rel.deploy(release_id=EXPECTED_CANDIDATE_RELEASE_ID, confirm=True, remote=after)
    except rel.ReleaseError as exc:
        assert "remote verification" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy should reject missing health ok after deploy")
    assert after.uploads


def test_manifest_declares_exact_runtime_modes():
    manifest = rel.load_manifest()
    assert manifest["runtime_modes"] == {"V2": "off", "V3": "publish"}
    assert "mode_key" not in manifest
    assert "mode_value" not in manifest


def test_rollback_restores_and_removes_targets_on_first_error_and_redacts_secret():
    fake = FakeRemote(fail_compile=True, secret_error=True)
    try:
        rel.deploy(release_id=EXPECTED_CANDIDATE_RELEASE_ID, confirm=True, remote=fake)
    except rel.ReleaseError as exc:
        assert "sk-secret-value" not in str(exc)
        assert "[redacted]" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("deploy should fail")
    joined = "\n".join(fake.commands)
    assert "release_identity.json" in joined
    assert joined.count("systemctl --user restart novostroy-bot-api.service") == 1
    assert "novostroy-bot-n8n-bridge.service" not in joined
    assert fake.rolled_back


def test_rollback_verification_failure_surfaces_with_original_failure():
    fake = FakeRemote(fail_compile=True, secret_error=True, rollback_health={})
    try:
        rel.deploy(release_id=EXPECTED_CANDIDATE_RELEASE_ID, confirm=True, remote=fake)
    except rel.ReleaseError as exc:
        text = str(exc)
        assert "deploy failed:" in text
        assert "rollback failed:" in text
        assert "[redacted]" in text
    else:  # pragma: no cover
        raise AssertionError("deploy should fail with rollback verification error")


def test_local_import_smoke_includes_runtime_adapter_and_uses_five_modules():
    manifest = rel.load_manifest()
    lines = rel.local_compile_and_import(rel.targets_from_manifest(manifest))
    assert "import_compat modules=5" in lines
