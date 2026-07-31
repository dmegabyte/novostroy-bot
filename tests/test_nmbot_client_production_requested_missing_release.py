import asyncio
import importlib.util
import json
import py_compile
import sys
from hashlib import sha256
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUNDLE_ROOT = PROJECT_ROOT / "release_bundles" / "client_production_requested_missing"
CANDIDATE_PATH = BUNDLE_ROOT / "candidates" / "nmbot_v2" / "response_composer.py"
MANIFEST_PATH = BUNDLE_ROOT / "manifest.json"
BASELINE_PATH = Path("/tmp/opencode/nmbot-client-production-response_composer.live.py")
BASELINE_SHA = "8a3d4e3c55aba6038fdf141773bea7cb0c771670e6cc94b0effc777323170cce"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from nmbot_v2.contracts import (  # noqa: E402
    ComposedResponse,
    ExecutableTurn,
    ExecutionResult,
    IntentGoal,
    OptionCard,
    ResponseBrief,
    ResponsePlan,
    SearchResult,
    Stage,
    StateDelta,
    TurnAction,
)
from nmbot_v2.state import ConversationState  # noqa: E402


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _candidate_module():
    spec = importlib.util.spec_from_file_location("nmbot_v2.response_composer_client_release", CANDIDATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert Path(module.__file__).resolve() == CANDIDATE_PATH.resolve()
    return module


def _open_question_plan(*, requested=(), facts_needed=(), viewpoint="life") -> ExecutableTurn:
    return ExecutableTurn(
        goal=IntentGoal.ANSWER_OPEN_QUESTION,
        stage=Stage.CURRENT_OPTIONS,
        action=TurnAction.ANSWER_FROM_CURRENT_OPTIONS,
        query_text="Какие условия?",
        viewpoint=viewpoint,
        intent=viewpoint,
        requested_facts=tuple(requested),
        facts_needed=tuple(facts_needed),
        resolved_subject="mortgage",
    )


def _brief(module, plan: ExecutableTurn, cards: tuple[OptionCard, ...]) -> ResponseBrief:
    return module.build_response_brief(
        stage=plan.stage,
        plan=plan,
        execution=ExecutionResult(ok=True),
        delta=StateDelta(),
        state=ConversationState(visible_options=cards),
        response_plan=ResponsePlan(acknowledgement="Да.", cards=cards, viewpoint=plan.viewpoint, final_question="Что проверить дальше?"),
    )


def test_manifest_records_baseline_and_candidate_hashes_exactly():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    artifact = manifest["artifacts"][0]

    assert manifest["release_id"] == "2026-07-24.client-production-requested-missing-policy.1"
    assert manifest["contour"] == "client_production"
    assert manifest["remote_root"] == "/home/neiro/novostroy-bot-client-production"
    assert manifest["bridge_restart"] is False
    assert manifest["deployable"] is False
    assert manifest["status"] == "prepared_only"
    assert artifact["expected_remote_base_sha256"] == BASELINE_SHA
    assert _sha(BASELINE_PATH) == BASELINE_SHA
    assert artifact["candidate_sha256"] == _sha(CANDIDATE_PATH)


def test_candidate_is_minimal_overlay_without_writer_formatter_pipeline():
    source = CANDIDATE_PATH.read_text(encoding="utf-8")

    assert "WRITER_PROMPT_PATH" not in source
    assert "FORMATTER_PROMPT_PATH" not in source
    assert "compose_response_writer_formatter_async" not in source
    assert "formatter_request_payload" not in source


def test_candidate_py_compile_and_manifest_json_parse():
    py_compile.compile(str(CANDIDATE_PATH), doraise=True)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["artifacts"][0]["candidate_path"] == "candidates/nmbot_v2/response_composer.py"


def test_first_list_broad_search_missing_stays_out_of_client_brief():
    module = _candidate_module()
    card = OptionCard(name="ЖК Лучи", price="от 12 млн рублей", ads_count=7)

    brief = module.build_response_brief(
        stage=Stage.FIRST_LIST,
        plan=ExecutableTurn(goal=IntentGoal.NEW_SEARCH, stage=Stage.FIRST_LIST, action=TurnAction.SEARCH, query_text="Подберите варианты", viewpoint="life", intent="life"),
        execution=ExecutionResult(ok=True, search=SearchResult(facts=(card,), missing=("finance", "ads"))),
        delta=StateDelta(),
        state=ConversationState(),
        response_plan=ResponsePlan(acknowledgement="Нашла.", viewpoint="life", final_question="Какой вариант смотрим?"),
    )

    assert brief.requested_facts == ()
    assert brief.missing_facts == ()
    assert brief.canonical_missing_summary == ()


def test_facts_needed_only_is_for_scenario_context_not_client_missing():
    module = _candidate_module()
    brief = _brief(module, _open_question_plan(facts_needed=("mortgage_terms",), viewpoint="financing"), (OptionCard(name="ЖК Лучи", mortgage_terms="семейная ипотека"),))

    assert brief.requested_facts == ()
    assert brief.missing_facts == ()
    assert brief.canonical_missing_summary == ()
    assert "mortgage_terms" in json.dumps(brief.scenario_context, ensure_ascii=False)


def test_explicit_missing_mortgage_gets_operator_consent_policy_and_template():
    module = _candidate_module()
    brief = _brief(module, _open_question_plan(requested=("mortgage_terms",), viewpoint="financing"), (OptionCard(name="ЖК Лучи"),))

    assert brief.requested_facts == ("mortgage_terms",)
    assert brief.missing_facts == ("mortgage_terms",)
    assert brief.canonical_missing_summary == ("mortgage_terms",)
    assert brief.response_policy == "operator_consent_offer"
    assert brief.operator_handoff_template
    assert module._missing_note_required(brief) is True


def test_explicit_available_mortgage_does_not_trigger_missing_policy():
    module = _candidate_module()
    brief = _brief(module, _open_question_plan(requested=("mortgage_terms",), viewpoint="financing"), (OptionCard(name="ЖК Лучи", mortgage_terms="семейная ипотека"),))

    assert brief.requested_facts == ("mortgage_terms",)
    assert brief.available_facts == ("mortgage_terms",)
    assert brief.missing_facts == ()
    assert brief.response_policy == ""
    assert brief.operator_handoff_template == ""
    assert module._missing_note_required(brief) is False


def test_compose_response_sync_strips_unsolicited_missing_note_without_explicit_missing():
    module = _candidate_module()
    brief = ResponseBrief(answer_goal="answer_open_question", canonical_cards=(), fallback_question="Что проверить дальше?")

    def composer(_brief, *, repair_errors=(), model=None):
        return {
            "intro": "Могу ответить по текущим данным.",
            "options": [],
            "recommendation": "",
            "missing_note": "Лишняя оговорка про неподтверждённые условия.",
            "final_question": "Что проверить дальше?",
        }

    result = module.compose_response_sync(brief, fallback_text="fallback", composer=composer)

    assert result.status == "primary"
    assert "Лишняя оговорка" not in result.text
    assert result.text == "Могу ответить по текущим данным.\n\nЧто проверить дальше?"


def test_async_composer_paths_strip_unsolicited_missing_note_without_explicit_missing():
    module = _candidate_module()
    module.PROMPT_PATH = PROJECT_ROOT / "prompts" / "v2_response_composer.txt"
    brief = ResponseBrief(answer_goal="answer_open_question", canonical_cards=(), fallback_question="Что проверить дальше?")

    async def composer(_brief, *, repair_errors=(), model=None):
        return {
            "intro": "Могу ответить по текущим данным.",
            "options": [],
            "recommendation": "",
            "missing_note": "Лишняя оговорка про неподтверждённые условия.",
            "final_question": "Что проверить дальше?",
        }

    async def run_paths():
        regular = await module.compose_response_async(brief, fallback_text="fallback", composer=composer)
        one_shot = await module.compose_response_one_shot_async(brief, fallback_text="fallback", composer=composer)
        return regular, one_shot

    regular, one_shot = asyncio.run(run_paths())
    assert regular.status == "primary"
    assert one_shot.status == "primary"
    assert "Лишняя оговорка" not in regular.text
    assert "Лишняя оговорка" not in one_shot.text
