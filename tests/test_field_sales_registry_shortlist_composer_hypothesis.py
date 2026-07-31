from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "field_sales_registry" / "v1"
SPEC = importlib.util.spec_from_file_location("shortlist_composer_hypothesis", REGISTRY / "shortlist_composer_hypothesis.py")
composer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(composer)

RUNNER_SPEC = importlib.util.spec_from_file_location("shortlist_composer_matrix", REGISTRY / "run_shortlist_composer_matrix.py")
runner = importlib.util.module_from_spec(RUNNER_SPEC)
assert RUNNER_SPEC.loader is not None
RUNNER_SPEC.loader.exec_module(runner)


def matrix():
    return json.loads((REGISTRY / "shortlist_composer_matrix.json").read_text(encoding="utf-8"))


def case(case_id: str):
    return next(item for item in matrix() if item["case_id"] == case_id)


def family_case():
    item = case("shortlist_sparse_family_three_options")
    return copy.deepcopy(item["input"]), copy.deepcopy(item["candidate"])


def result_for(candidate, data=None):
    source, default_candidate = family_case()
    return composer.simulate(data or source, candidate or default_candidate)


def assert_error(code, candidate, data=None):
    result = result_for(candidate, data)
    assert result["valid"] is False
    assert code in result["errors"]
    assert result["text"] == ""
    assert "Синтетический Бусиновский" not in json.dumps(result, ensure_ascii=False) or code != "object_name_mismatch"


def test_context_derives_shared_fields_roles_and_exact_delta_121340():
    data, _candidate = family_case()

    context = composer.derive_comparison_context(data)

    assert context["shared_field_ids"] == ["school", "kindergarten", "ready"]
    assert context["price_deltas"]["1:2"] == 121340
    assert context["options"][0]["decision_role"] == "lowest_price"
    assert context["options"][1]["decision_role"] == "middle_price/location_choice"
    assert context["options"][2]["decision_role"] == "highest_price/location_choice"
    assert {
        "other_object_name": "ЖК Синтетический Лосиный",
        "relation": "cheaper",
        "delta": 121340,
    } in context["options"][1]["price_comparisons"]
    assert context["options"][1]["primary_price_comparison"] == {
        "other_object_name": "ЖК Синтетический Лосиный",
        "relation": "cheaper",
        "delta": 121340,
    }
    assert "одну ближайшую" in context["options"][1]["role_instruction"]


def test_valid_family_candidate_has_shared_facts_once_and_distinct_roles():
    data, candidate = family_case()
    result = composer.simulate(data, candidate)

    assert result["valid"] is True
    assert result["manual_review_required"] is True
    assert result["metadata"]["decision_roles"] == ["lowest_price", "middle_price/location_choice", "highest_price/location_choice"]
    text = result["text"]
    assert text.count("школ") == 1
    assert text.count("детский сад") == 1
    assert "121 340" in text
    assert text.count("?") == 1
    assert text.rstrip().endswith(data["cta_template"])
    assert "1. ЖК Синтетический Бусиновский\n" in text
    assert ".;" not in text
    assert ".." not in text
    assert "В Бусиновском районе цена квартиры — 12 417 930 рублей." in text


def test_model_package_is_sanitized_and_contains_strict_contract():
    data, _candidate = family_case()
    data["options"][0]["diagnostics"] = {"source_path": "forbidden"}
    with_extra = copy.deepcopy(data)
    package = composer.build_model_input(family_case()[0])
    dumped = json.dumps(package, ensure_ascii=False)

    assert package["input"]["answer_goal"] == "present_shortlist"
    assert package["output_contract"]["max_options"] == 3
    assert package["output_contract"]["option_required_keys"] == ["object_name", "presentation", "decision_role", "used_field_ids"]
    assert "prompt_sha256" in package
    assert "diagnostics" not in dumped
    assert "source_path" not in dumped
    assert "121340" in dumped
    try:
        composer.build_model_input(with_extra)
    except ValueError:
        pass
    else:
        raise AssertionError("extra option keys must be rejected")


def test_invalid_common_fact_repetition_and_high_overlap_presentations_fail_closed():
    item = case("shortlist_negative_repeated_common_facts")
    result = composer.simulate(copy.deepcopy(item["input"]), copy.deepcopy(item["candidate"]))

    assert result["valid"] is False
    assert "common_fact_repeated" in result["errors"]
    assert "duplicate_presentation" in result["errors"]
    assert result["text"] == ""


def test_unique_terrace_appears_once_in_presentation():
    item = case("shortlist_one_true_unique_feature")
    result = composer.simulate(copy.deepcopy(item["input"]), copy.deepcopy(item["candidate"]))

    assert result["valid"] is True
    assert item["candidate"]["options"][1]["presentation"].lower().count("террас") == 1


def test_new_positive_shortlist_cases_have_expected_roles_and_literal_numbers():
    expected = {
        "shortlist_financing_budget_two_options": ["lowest_price", "highest_price/unique_fact"],
        "shortlist_parking_vs_lower_price": ["highest_price/unique_fact", "lowest_price"],
        "shortlist_investment_literal_counters": ["lowest_price", "no_unique_advantage"],
    }
    literals = {
        "shortlist_financing_budget_two_options": ["9 800 000", "10 400 000", "8,9%", "7,8%", "2 200 000", "3 000 000", "18 месяцев", "24 месяца"],
        "shortlist_parking_vs_lower_price": ["11 200 000", "10 500 000", "1 200 000", "7 машиномест"],
        "shortlist_investment_literal_counters": ["9 200 000", "9 900 000", "14 продаж ЕГРН", "38 объявлений на витрине", "21 продажа ЕГРН", "29 объявлений на витрине"],
    }

    for case_id, roles in expected.items():
        item = case(case_id)
        context = composer.derive_comparison_context(copy.deepcopy(item["input"]))
        result = composer.simulate(copy.deepcopy(item["input"]), copy.deepcopy(item["candidate"]))

        assert [option["decision_role"] for option in context["options"]] == roles
        assert result["valid"] is True, result["errors"]
        assert result["metadata"]["decision_roles"] == roles
        for literal in literals[case_id]:
            assert literal in result["text"]

    investment_context = composer.derive_comparison_context(case("shortlist_investment_literal_counters")["input"])
    assert {"sales_count", "ads_count"} <= set(investment_context["common_field_ids"])
    assert "sales_count" not in investment_context["shared_field_ids"]


def test_new_field_mentions_must_be_declared_and_available():
    item = case("shortlist_financing_budget_two_options")
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["used_field_ids"].remove("mortgage_rate")
    assert_error("undeclared_field_claim", candidate, copy.deepcopy(item["input"]))

    item = case("shortlist_parking_vs_lower_price")
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][1]["presentation"] += " Паркинг тоже указан."
    assert_error("unavailable_field_claim", candidate, copy.deepcopy(item["input"]))

    item = case("shortlist_investment_literal_counters")
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["used_field_ids"].remove("ads_count")
    assert_error("undeclared_field_claim", candidate, copy.deepcopy(item["input"]))


def test_new_scenario_forbidden_claims_are_rejected():
    item = case("shortlist_financing_budget_two_options")
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["presentation"] += " Ипотека будет одобрена, это лучшая ставка и переплаты не будет."
    assert_error("unsupported_claim", candidate, copy.deepcopy(item["input"]))

    item = case("shortlist_parking_vs_lower_price")
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["presentation"] += " Машиноместо можно забронировать, места точно останутся."
    assert_error("unsupported_claim", candidate, copy.deepcopy(item["input"]))

    item = case("shortlist_investment_literal_counters")
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["presentation"] += " Это даёт доходность, ликвидность и рост цен."
    assert_error("unsupported_claim", candidate, copy.deepcopy(item["input"]))


def test_duplicate_presentations_fail_closed():
    data, candidate = family_case()
    candidate["options"][1]["presentation"] = candidate["options"][0]["presentation"]
    assert_error("duplicate_presentation", candidate, data)


def test_unsupported_proximity_and_immediate_move_claims_are_rejected():
    item = case("shortlist_negative_unsupported_claims")
    result = composer.simulate(copy.deepcopy(item["input"]), copy.deepcopy(item["candidate"]))

    assert result["valid"] is False
    assert "unsupported_claim" in result["errors"]
    assert "unknown_number" in result["errors"]
    assert result["text"] == ""


def test_unavailable_or_undeclared_fact_claims_fail_closed():
    item = case("shortlist_one_true_unique_feature")
    data = copy.deepcopy(item["input"])
    candidate = copy.deepcopy(item["candidate"])
    candidate["intro"] = "Оба варианта имеют готовые корпуса, школу и детский сад."
    assert_error("unavailable_field_claim", candidate, data)

    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["presentation"] += " У варианта есть метро."
    assert_error("unavailable_field_claim", candidate, data)

    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][1]["used_field_ids"] = ["apartment_price"]
    assert_error("undeclared_field_claim", candidate, data)


def test_bureaucratic_sales_phrasing_is_rejected():
    data, candidate = family_case()
    candidate["options"][0]["presentation"] = "В Бусиновском районе цена квартиры — 12 417 930 рублей. Этот вариант является самым предпочтительным, если бюджет является ключевым фактором."
    assert_error("bureaucratic_style", candidate, data)

    data, candidate = family_case()
    candidate["options"][0]["presentation"] = "В Бусиновском районе цена квартиры — 12 417 930 рублей. Этот вариант привлекателен для ограниченного бюджета."
    assert_error("bureaucratic_style", candidate, data)

    data, candidate = family_case()
    candidate["options"][0]["presentation"] = "В Бусиновском районе цена квартиры — 12 417 930 рублей. Бюджет является важным фактором."
    assert_error("bureaucratic_style", candidate, data)

    data, candidate = family_case()
    candidate["options"][0]["presentation"] = "В Бусиновском районе цена квартиры — 12 417 930 рублей. Этот вариант будет предпочтительным."
    assert_error("bureaucratic_style", candidate, data)

    data, candidate = family_case()
    candidate["recommendation"] = "В остальных случаях выбор зависит от предпочтительного для вас района."
    assert composer.simulate(data, candidate)["valid"] is True


def test_recommendation_must_not_semantically_repeat_cta():
    item = case("shortlist_one_true_unique_feature")
    candidate = copy.deepcopy(item["candidate"])
    candidate["recommendation"] = "Выбор зависит от того, что для вас важнее: минимальная цена или наличие террасы."
    assert_error("recommendation_cta_repetition", candidate, copy.deepcopy(item["input"]))

    candidate["recommendation"] = ""
    assert composer.simulate(copy.deepcopy(item["input"]), candidate)["valid"] is True


def test_unknown_numbers_and_invented_comparative_numbers_are_rejected():
    data, candidate = family_case()
    candidate["options"][1]["presentation"] = "В Очаково-Матвеевском цена квартиры — 14 307 660 рублей. Он подходит для этой локации и на 222 222 рубля дешевле варианта в Метрогородке."

    result = composer.simulate(data, candidate)

    assert result["valid"] is False
    assert "unknown_number" in result["errors"]
    assert "invented_comparative_number" in result["errors"]

    investment = case("shortlist_investment_literal_counters")
    assert "invented_comparative_number" not in composer.validate_candidate(investment["input"], investment["candidate"])


def test_strict_candidate_schema_names_order_and_cta_are_enforced():
    data, candidate = family_case()
    bad = copy.deepcopy(candidate)
    bad["options"][0]["extra"] = "nope"
    assert_error("candidate_schema", bad, data)

    bad = copy.deepcopy(candidate)
    bad["options"] = [bad["options"][1], bad["options"][0], bad["options"][2]]
    assert_error("option_order_mismatch", bad, data)

    bad = copy.deepcopy(candidate)
    bad["final_question"] = "Разобрать подробнее?"
    assert_error("cta_mismatch", bad, data)

    bad = copy.deepcopy(candidate)
    bad["options"][0]["presentation"] = "ЖК Синтетический Бусиновский в Бусиновском районе стоит 12 417 930 рублей."
    assert_error("option_name_repeated", bad, data)

    bad = copy.deepcopy(candidate)
    bad["options"][0]["presentation"] = "Первое предложение. Второе предложение. Третье предложение. Четвертое предложение."
    assert_error("text_bounds", bad, data)


def test_used_field_ids_must_exist_and_be_grounded_to_that_option():
    data, candidate = family_case()
    bad = copy.deepcopy(candidate)
    bad["options"][0]["used_field_ids"] = ["unknown"]
    assert_error("unknown_field_id", bad, data)

    bad = copy.deepcopy(candidate)
    bad["options"][0]["used_field_ids"] = ["apartment_price"]
    bad["options"][0]["presentation"] = "В Бусиновском районе это самый низкий вход по бюджету."
    assert_error("ungrounded_field", bad, data)


def test_known_location_inflection_is_grounded_but_unrelated_location_is_not():
    data, candidate = family_case()
    inflected = copy.deepcopy(candidate)
    inflected["options"][0]["presentation"] = "В Бусиновском районе цена квартиры — 12 417 930 рублей. Это самый низкий вход по цене в этой тройке, поэтому его логично смотреть первым, если бюджет важнее выбора конкретной локации."
    inflected["options"][2]["presentation"] = "В Метрогородке цена квартиры — 14 429 000 рублей. Его стоит выбирать из-за нужной локации, но ценового преимущества перед двумя другими вариантами у него нет."
    assert composer.simulate(data, inflected)["valid"] is True

    unrelated = copy.deepcopy(candidate)
    unrelated["options"][0]["presentation"] = "Цена квартиры — 12 417 930 рублей. Самая низкая цена; район не назван."
    assert_error("ungrounded_field", unrelated, data)


def test_no_unique_advantage_same_values_with_different_locations():
    item = case("shortlist_no_unique_advantage_same_values")
    context = composer.derive_comparison_context(item["input"])
    result = composer.simulate(item["input"], item["candidate"])

    assert [option["decision_role"] for option in context["options"]] == ["no_unique_advantage", "no_unique_advantage"]
    assert result["valid"] is True

    intro_price_only = copy.deepcopy(item["candidate"])
    intro_price_only["options"][0]["presentation"] = "Локация — Восточный район. Отдельного преимущества по фактам нет; вариант имеет смысл выбирать только если нужна эта локация."
    intro_price_only["options"][0]["used_field_ids"] = ["location"]
    intro_price_only["options"][1]["presentation"] = "Локация — Западный район. По тем же фактам он не сильнее первого; честный выбор здесь зависит от нужной локации."
    intro_price_only["options"][1]["used_field_ids"] = ["location"]
    assert composer.simulate(copy.deepcopy(item["input"]), intro_price_only)["valid"] is True


def test_scenario_specific_fields_cannot_be_silently_omitted():
    for case_id, required_field in (
        ("shortlist_financing_budget_two_options", "mortgage_rate"),
        ("shortlist_parking_vs_lower_price", "parking_inventory"),
        ("shortlist_investment_literal_counters", "ads_count"),
    ):
        item = case(case_id)
        candidate = copy.deepcopy(item["candidate"])
        option_index = next(idx for idx, option in enumerate(candidate["options"]) if required_field in option["used_field_ids"])
        candidate["options"][option_index]["used_field_ids"].remove(required_field)
        assert_error("scenario_field_coverage_missing", candidate, copy.deepcopy(item["input"]))


def test_numeric_field_requires_exact_value_and_parking_availability_wording_is_blocked():
    item = case("shortlist_parking_vs_lower_price")
    data = copy.deepcopy(item["input"])
    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["presentation"] = "Этот вариант дороже на 700 000 рублей; указан паркинг за 1 200 000 рублей и 7 машиномест."
    assert_error("ungrounded_field", candidate, data)

    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][0]["presentation"] = candidate["options"][0]["presentation"].replace("остаток — 7 машиномест", "в наличии 7 машиномест")
    assert_error("unsupported_claim", candidate, data)


def test_investment_counters_cannot_justify_price_and_require_literal_caveat():
    item = case("shortlist_investment_literal_counters")
    data = copy.deepcopy(item["input"])
    candidate = copy.deepcopy(item["candidate"])
    candidate["intro"] = "Сравним цену и показатели двух вариантов."
    candidate["options"][0]["presentation"] = candidate["options"][0]["presentation"].replace(
        "а счётчики нужно читать буквально: без вывода о спросе, доходе, росте цены или будущей продаже.",
        "эти показатели можно сопоставить между собой.",
    )
    candidate["options"][1]["presentation"] = candidate["options"][1]["presentation"].replace(
        "Ценового преимущества нет; это буквальные счётчики без инвестиционного вывода.",
        "Больше продаж и меньше объявлений оправдывают более высокую цену.",
    )
    assert_error("investment_counter_inference", candidate, data)
    assert_error("investment_counter_caveat_missing", candidate, data)

    candidate = copy.deepcopy(item["candidate"])
    candidate["options"][1]["presentation"] += " Эти показатели делают второй вариант интереснее."
    assert_error("investment_counter_inference", candidate, data)

    context = composer.derive_comparison_context(data)
    assert context["options"][1]["decision_role"] == "no_unique_advantage"

    safe = copy.deepcopy(item["candidate"])
    safe["intro"] = "Это буквальные счётчики без рыночного или финансового прогноза и без вывода о будущем результате."
    assert composer.simulate(data, safe)["valid"] is True

    price_reason = copy.deepcopy(safe)
    price_reason["options"][0]["presentation"] += " Если важна минимальная цена, этот вариант стоит рассмотреть."
    assert composer.simulate(data, price_reason)["valid"] is True


def test_runner_matrix_is_deterministic_and_has_expected_coverage():
    report = runner.generate_report(matrix())

    assert len(report) == 8
    assert [item["case_id"] for item in report] == [item["case_id"] for item in matrix()]
    assert sum(1 for item in report if item["valid"]) == 6
    assert all(item["manual_review_required"] is True for item in report)
    runner.validate_or_raise(report, matrix())


def test_cli_print_model_input_for_one_case():
    proc = subprocess.run(
        [sys.executable, str(REGISTRY / "run_shortlist_composer_matrix.py"), "--case", "shortlist_sparse_family_three_options", "--print-model-input"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    package = json.loads(proc.stdout)
    assert package["input"]["comparison_context"]["price_deltas"]["1:2"] == 121340


def test_source_isolation_no_runtime_or_nmbot_imports():
    source = (REGISTRY / "shortlist_composer_hypothesis.py").read_text(encoding="utf-8")

    forbidden = ["nmbot_v0", "nmbot_v2", "chat_tester_bot", "requests", "openai", "anthropic", "httpx"]
    assert not any(token in source for token in forbidden)
