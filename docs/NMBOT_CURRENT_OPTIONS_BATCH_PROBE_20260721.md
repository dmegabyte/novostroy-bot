# NMBOT current-options batch probe — 2026-07-21

## Назначение

Read-only production-shaped probe для проверки, достаточно ли одного typed
MCP-запроса, чтобы ответить на follow-up по уже показанным ЖК.

Runtime и production этим probe не изменялись.

## Request

```text
search_mode: current_options_fact_check
current_option_names:
  - Бусиновский парк
  - Лосиноостровский парк
  - Мичуринский парк
facts_needed:
  - parks
  - schools
viewpoint: family
base_viewpoint: life
count: 3
```

## Gateway / contract result

```text
gateway: ok
contract: valid
scope: valid
facts: 3
near: 0
missing: 0
foreign_objects: 0
```

## Normalized MCP facts

| ЖК | id | location | school | kindergarten | park_near | water_near | min_price | max_price | delivered | ready_quarter | built_year |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Бусиновский парк | 2332 | Западное Дегунино | 1 | 1 | 1 | 1 | 12 417 930 | 36 706 253 | 1 | 2 | 2027 |
| Лосиноостровский парк | 2411 | Метрогородок | 1 | 1 | 1 | 1 | 14 429 000 | 32 604 840 | 1 | 1 | 2028 |
| Мичуринский парк | 2401 | Очаково-Матвеевское | 1 | 1 | 1 | 1 | 14 307 660 | 37 638 510 | 1 | 3 | 2028 |

MCP diagnostics stated that school and park facts were confirmed for all
requested ЖК. The request also carried the expected family and life field
priorities, including `school`, `kindergarten`, `park_near`, `water_near`,
`property_metro`, readiness, finishing and infrastructure fields.

## What this is enough to say

All three current options have confirmed school, kindergarten, park-near and
water-near flags. They can be presented as comparable family/walkability
options, with price and location differences.

## What this is not enough to say

The result does not contain:

- a park name;
- distance or walking time to a park;
- a per-card ranking by park proximity.

Therefore the client-facing answer may say that all three have confirmed nearby
park/water evidence, but must not claim which ЖК is closest to a specific park.

## Normalizer discrepancy

Wire facts use numeric flags (`1`). The current normalizer implementation checks
infrastructure flags with `raw.get(key) is True`. As a result, the same response
became `OptionCard.infrastructure=()` for all three cards.

This is a normalization boundary defect:

```text
MCP has evidence
→ normalizer drops numeric boolean evidence
→ OptionCard has no infrastructure
→ renderer falls back to generic card text
```

The fix belongs in `nmbot_v2/card_normalizer.py`; it should normalize `1/0`
without treating the ЖК name word «парк» as evidence. No additional MCP request
is needed to solve this loss.

## Renderer check

With the same three cards and restored infrastructure values, the existing
`nmbot_v2/response.py` renderer produced separate blocks with unique grounded
reasons. Because the facts are identical across all three ЖК, the final answer
must state that the options are comparable on this criterion rather than invent
different advantages.

## Decision

One batch fact-check is sufficient for this observed case. A second bounded
request should be reserved for structural underfill (missing exact objects), not
for facts that MCP explicitly reports as absent.

## Customer-facing structured output contract

The internal statement “all options are comparable and exact distance is
missing” remains a safety verdict only. It must be transformed into a useful
customer recommendation using a confirmed secondary difference.

```json
{
  "intro": "...",
  "options": [{"name": "...", "facts": "...", "description": "..."}],
  "recommendation": "...",
  "missing_note": "...",
  "final_question": "..."
}
```

`recommendation` is required for `recommend_current`; the communication model
may phrase it but may not invent evidence or expose the internal verdict. The
full golden example is `docs/GOLDEN_DIALOGS.md`, Example 7.
