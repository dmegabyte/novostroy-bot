# Golden examples for V2 MCP search contract

Статус: compact readable goldens для инженерной проверки MCP search contract.
Все значения синтетические: это не live-каталог, не обещание наличия и не
пример реальных цен.

Канон исполнения остаётся в `nmbot_v2/search_contract.py` и
`tests/fixtures/v2_search_mcp_contract.json`. Эти примеры нужны, чтобы быстро
понять ожидаемый статус validator: `valid`, `degraded` или `invalid`.

## 1. Broad search — valid

```json
{
  "facts": [{"id": "synthetic-1", "name": "ЖК Синтетический", "district": "msk", "location": "Тестовая локация"}],
  "near": [],
  "missing": [],
  "params": {},
  "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "life", "base_viewpoint": null, "requested_field_priorities": ["location", "district", "ready"], "relaxation_audit": [], "ignored_preferences": [], "notes": []}
}
```

Expected validator status: `valid`. Верхний уровень точный, diagnostics полные,
hard-фильтров нет, карточка идентифицируема.

## 2. Named object — valid

Request delta: `search_mode=named_object`, `entity_reference="ЖК Синтетический"`,
`lookup_mode=exact_named_object`, `count=1`.

Expected response: один exact object в `facts`, `near=[]`. Expected validator
status: `valid`; похожие ЖК не подмешиваются.

## 3. Current options fact-check — valid

Request delta: `search_mode=current_options_fact_check`,
`current_option_names=["ЖК Альфа", "ЖК Бета"]`, `facts_needed=["parking"]`.

```json
{
  "facts": [{"id": "alpha", "name": "ЖК Альфа", "parking": true}],
  "near": [{"id": "beta", "name": "ЖК Бета", "is_near": true, "why_close": "паркинг не подтверждён", "differences": ["parking"]}],
  "missing": ["parking"],
  "params": {},
  "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "life", "base_viewpoint": null, "requested_field_priorities": ["parking"], "relaxation_audit": [], "ignored_preferences": [], "notes": []}
}
```

Expected validator status: `valid`; current-options validator тоже проходит,
потому что все имена входят в `current_option_names`.

## 4. Family + financing overlay — valid

Request delta: `response_viewpoint=financing`, `base_viewpoint=family`,
`effective_hard={"rooms":[2]}`, `preferences.finance_preference=mortgage_details`.

Expected validator status: `valid`, если `facts` содержит structured `rooms`
evidence и finance-поля остаются overlay, а семейные поля не исчезают из
`requested_field_priorities`.

## 5. Near alternative — valid

```json
{
  "facts": [],
  "near": [{"id": "near-1", "name": "ЖК Почти", "rooms": [1], "location": "Соседняя локация", "why_close": "другая комнатность; другая локация", "differences": ["rooms", "location"], "is_near": true}],
  "missing": [],
  "params": {"rooms": [2], "location": ["Тестовая локация"]},
  "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "life", "base_viewpoint": null, "requested_field_priorities": ["rooms", "location"], "relaxation_audit": [], "ignored_preferences": [], "notes": []}
}
```

Expected validator status: `valid`: альтернатива не попала в `facts`, отличия
описаны в `near`.

## 6. Missing evidence — valid

```json
{
  "facts": [{"id": "missing-ok-1", "name": "ЖК Без части полей", "rooms": [3]}],
  "near": [],
  "missing": ["school", "kindergarten", "park_near"],
  "params": {"rooms": [3]},
  "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "family", "base_viewpoint": null, "requested_field_priorities": ["school", "kindergarten", "park_near"], "relaxation_audit": [], "ignored_preferences": [], "notes": []}
}
```

Expected validator status: `valid`: отсутствие сценарных полей записано как
`missing`, без claims вида «этого нет».

## 7. Missing hard evidence — retained and reported

```json
{
  "facts": [{"id": "reported-1", "name": "ЖК Без подтверждённой цены"}],
  "near": [],
  "missing": [],
  "params": {"max_price": 10000000},
  "diagnostics": {"mcp_tool": "novostroym/get_flat_info", "response_viewpoint": "life", "base_viewpoint": null, "requested_field_priorities": ["min_price", "max_price"], "relaxation_audit": [], "ignored_preferences": [], "notes": ["contract_warning:fact_missing_hard_evidence_reported"]}
}
```

Expected validator status: `invalid` with missing-hard-evidence error and bounded
`contract_warning:*`. Production keeps the identifiable card in `facts` and
logs the report; offline quality gate treats this scenario as failed.

## 8. Invalid — invalid

- лишний top-level `response` → `invalid` / `top_level_keys_mismatch`;
- diagnostics без семи обязательных ключей → `invalid`;
- объект в `facts` при активном `max_price`, но без price evidence → `invalid`;
- `absence_claim` / `inventory_absent` в `missing` или `notes` без evidence →
  `invalid`.

## Ownership note

- Схемы: `schemas/v2_search_mcp_request.schema.json`,
  `schemas/v2_search_mcp_response.schema.json`.
- Исполняемая проверка: `nmbot_v2/search_contract.py`.
- Если schema/golden расходится с executable validator, главным считается
  validator; artifacts надо синхронизировать отдельным contract change.
