# NMBot V2 — правила сборки MCP search prompt

Дата проверки: 2026-07-19.

Этот документ задаёт единый контракт сборки запроса к search-модели с MCP
`novostroym`. Он основан на контролируемой серии вызовов одной модели и одного
MCP при неизменных пользовательских условиях.

## 1. Вывод серии тестов

Контрольный запрос: `квартира для жизни в Москве до 40 млн`.

| System prompt | Query shape | Результат | Вывод |
|---|---|---|---|
| V1 search | compact envelope + `Клиент:` | 3 facts, но старые synthetic-поля | Поиск работает, output contract устарел |
| V2 search | compact envelope + `Клиент:` | 3 facts, strict V2 PASS | Целевой вариант |
| minimal | compact envelope + `Клиент:` | 15 facts, часть полей потеряна | Prompt слишком слабый |
| V2 search | большой `V2_SEARCH_INPUT` в query | 0 facts | Query перегружен и конкурирует с system contract |
| minimal | большой typed contract + natural query | 3 facts | Natural query помогает, но слабый system prompt неустойчив |

Дополнительно проверены family, family+financing, rooms+budget+location,
delivered+finishing и district+human-location. Prompt не заменяет validator:
room-hard попытки иногда возвращали ту же карточку без `rooms`.

## 2. Минимальная архитектура запроса

```text
SYSTEM PROMPT
  └─ постоянные правила MCP, output schema, facts/near/missing, grounding

PER-TURN QUERY
  ├─ SEARCH_CONTRACT_ENVELOPE (короткий JSON)
  ├─ одна строка exact-match policy
  ├─ Текущие параметры: {...}
  └─ Клиент: <естественный запрос>
```

Не помещать полный длинный system contract повторно в per-turn query. Не
передавать два конкурирующих формата контракта одновременно.

## 3. Что находится в system prompt

1. обязательный вызов `novostroym/get_flat_info`;
2. запрет отвечать по памяти модели;
3. строгие top-level поля `facts`, `near`, `missing`, `params`, `diagnostics`;
4. `facts` — exact, `near` — только маркированные альтернативы;
5. поле возвращается только из runtime allowlist и только из MCP;
6. viewpoint задаёт приоритет полей, но не hard-фильтр;
7. никаких client copy и routing/action/target/scope;
8. никаких обещаний доходности, спроса, роста цены и финансовых условий без evidence.

## 4. Что находится в per-turn query

```json
{
  "contract": "v2_search_mcp_contract",
  "constraints": {
    "requested_hard": {},
    "effective_hard": {},
    "preferences": {},
    "relaxation_audit": []
  },
  "response_viewpoint": "life",
  "base_viewpoint": null,
  "available_fact_fields": [],
  "hard_evidence_requirements": {},
  "count": 3
}
```

После envelope обязательно передаются:

```text
Текущие параметры: {effective_hard + preferences}
Клиент: <redacted natural search query>
```

Естественный текст задаёт цель. `effective_hard` остаётся единственным
источником строгих границ; модель не выводит новые hard-фильтры из текста.

## 5. Geography normalization до model call

| Пользователь | requested_hard | effective_hard |
|---|---|---|
| Москва | `location=["Москва"]` | `district="msk"` |
| Новая Москва | `location=["Новая Москва"]` | `district="newmsk"` |
| Московская область | `location=["Московская область"]` | `district="mo"` |
| Сокол | `location=["Сокол"]` | `location=["Сокол"]` |

`district` — региональный код; `location` — район/город/локация.

## 6. Hard evidence map

| Hard field | Допустимое MCP evidence |
|---|---|
| `district` | `district` |
| `location` | `location`, `location_id` |
| `max_price` | `min_price`, комнатные цены, `ads.fullprice` |
| `rooms` | `rooms`, `apartment_types.rooms`, `ads.rooms` |
| `ready=delivered` | `delivered`, `ready`, `state`, `status` |
| `finishing` | `finishing`, `ads.renovation`, `house.finishing_list` |

Если evidence не пришёл, объект запрещено публиковать в `facts`. Он может быть
`near` либо источником `missing`. Нельзя утверждать, что инвентаря нет.

## 7. Двухэтапный поиск для слабого evidence

Room/financing/lot-level hard-фильтры нельзя подтверждать общей карточкой ЖК:

1. broad MCP search получает кандидатов;
2. bounded enrichment запрашивает `rooms/apartment_types/ads` или finance;
3. runtime повторно применяет hard validator;
4. только подтверждённые карточки становятся `facts`.

Prompt может запросить evidence, но не гарантирует его в каждой попытке.

## 8. Один источник схемы

Allowlist полей, wire aliases и hard matchers импортируются из
`nmbot_v2/search_contract.py`. Probe, quality gate и runtime не держат копии.

Тестовая копия allowlist уже создала ложный FAIL на `ecology_rating`; поэтому
дублирование schema constants запрещено.

## 9. Runtime-owned поля

Модель не владеет diagnostics, viewpoints, relaxation audit, ignored
preferences и routing/state decisions. Runtime собирает их после ответа.
Модель владеет только `facts/near/missing/params`.

## 10. Обязательный release gate

1. prompt matrix при одинаковых MCP/model/query;
2. strict contract tests;
3. family, financing, rooms+budget+location, ready+finishing, district+location;
4. room-hard с enrichment;
5. SearchResult → CanonicalOptionCard → answer quality;
6. live Jivo stateful и compare gate.

Наличие названий ЖК при contract errors не является PASS.

## Source refs

- `prompts/v2_search_mcp.txt`
- `nmbot_v2/search_contract.py`
- `docs/NOVOSTROYM_MCP_SCHEMA.md`
- `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md`
- `scripts/nmbot_v2_mcp_prompt_matrix.py`
- `scripts/nmbot_v2_mcp_winning_prompt_series.py`
- `scripts/nmbot_four_layer_e2e.py:491-526`
