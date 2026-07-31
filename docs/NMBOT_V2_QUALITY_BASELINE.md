# Nmbot V2 — quality baseline

Эталон V2: `v2-baseline-20260721-family-enrichment-repair`.

Это отдельный baseline typed V2 runtime. Он не является доказательством V0 и
не меняет live selector: активную production-версию всегда нужно проверять
через `/api/runtime-version`.

Client-facing name V2: **Ирина**.

## Зафиксированное состояние

- Дата фиксации: 2026-07-21.
- V2 family broad search делает bounded exact-name enrichment первых трёх
  карточек.
- При отсутствии family-фактов исходная карточка сохраняется с другими
  подтверждёнными полями.
- Response composer делает не более одной repair-попытки для `semantic/schema`
  ошибок; transport/provider ошибки не ретраятся.
- При успешной repair composer status равен `repaired`; при повторной ошибке
  используется deterministic fallback.

## Эталонные проверки

```bash
python3 -m py_compile \
  nmbot_v2/response_composer.py \
  nmbot_v2/search_enrichment.py \
  scripts/nmbot_runtime_adapter.py

pytest -q tests/test_nmbot_v2_quality.py \
  tests/test_nmbot_v2_search_enrichment.py \
  tests/test_nmbot_runtime_adapter.py \
  -k 'not underfilled_broad_search_supplements_near_cards'
# 139 passed, 1 deselected

python3 scripts/nmbot_v2_quality_gate.py --case family --live --timeout 90
# family: PASS, score=10, hard blockers=0
```

Live family evidence at fixation:

| Поле | Значение |
|---|---|
| `facts` | 3 |
| enrichment | 3/3 applied |
| composer status | `repaired` |
| reliability | 7/10: одна успешная repair-попытка |
| latency | 27.092 s; 8/10 |
| overall | 10 |
| verdict | `PASS` |

## Правило сравнения

Любое изменение V2 сравнивается с этим baseline через V2 quality gate,
V2-specific regression и, для production claims, отдельный Jivo live evidence.
V0 tests/fixtures не засчитываются как V2 proof.

Источники: `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md`,
`docs/NMBOT_V2_PROJECT_QUALITY_SCORECARD.md`,
`nmbot_v2/search_enrichment.py`, `nmbot_v2/response_composer.py`.
