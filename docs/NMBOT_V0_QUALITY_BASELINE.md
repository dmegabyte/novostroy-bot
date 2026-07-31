# NMBot V0 — quality baseline

Эталон V0: `v0-baseline-20260721`.

Этот baseline относится только к независимому V0 runtime. Он не является
доказательством качества V2 и не должен заменяться результатами V2-тестов.

## Зафиксированное состояние

- Runtime: `nmbot_v0/runtime.py`.
- Contracts/state: `nmbot_v0/contracts.py`.
- Prompts: `prompts/v0_scenario_search.txt`, `prompts/v0_answer.txt`.
- Harness: `scripts/nmbot_v0_test_harness.py`.
- Namespace: `nmbot_v0`.
- Дата фиксации: 2026-07-21.

## Эталонные проверки

```bash
PYTHONPATH=. pytest -q \
  tests/test_nmbot_v0_runtime.py \
  tests/test_nmbot_v0_test_harness.py
# 23 passed

python3 scripts/nmbot_v0_test_harness.py --scenario all --json
# ok: true
```

Harness подтверждает отдельные сценарии `successful_flow`, `missing_fact` и
`unknown_card`. В `unknown_card` ожидаемо используется безопасный fallback с
передачей вопроса оператору; это не считается успешным ответом по карточке.

## Правило сравнения

Любое изменение V0 сравнивается с этим baseline через собственный V0 gate,
compile-проверку и отдельный Jivo smoke. V2 tests, enrichment и V2 composer не
влияют на V0 baseline.

Источники: `docs/NMBOT_V0.md`,
`docs/NMBOT_RUNTIME_VERSIONS.md`.
