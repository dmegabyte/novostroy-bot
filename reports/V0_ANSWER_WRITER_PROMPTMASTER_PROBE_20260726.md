# V0 Answer Writer PromptMaster Probe — 2026-07-26

Изолированная diagnostic probe переписанного PromptMaster prompt. Это не production и не Promptfoo eval.

Все assignments, имена и факты синтетические; outputs сравнимы с prior probe только как диагностический sample.

## Source references

- API client: `scripts/bluesminds_client.py` (`BluesmindsClient.chat(...)`).
- Baseline/prior assignments source: `data/v0_answer_writer_probe/bluesminds_20260726.jsonl` plus same 12 synthetic assignments from prior probe.

## Run metadata

- Timestamp UTC: `2026-07-26T13:26:14.816464+00:00`
- Model: `gpt-5.2-chat`
- Timeout: `180` seconds
- Temperature: `0.35`
- Max tokens: `500`
- Prompt SHA256: `da20d82a8a1c14d42bfa4344cb08b7e7906ba3148fe96d1a1f0311385673ebb1`
- Calls attempted: `5`
- Successful shaped nonempty cases: `4`
- First-case outcome: `valid_shape_nonempty`
- Stop reason: `subsequent_api_error`

## Exact system prompt

```text
Ты пишешь финальный ответ клиенту на русском языке по готовому assignment.

Assignment содержит:
client_message — текущее сообщение клиента;
recent_dialogue — недавний диалог;
response_job — какую работу должен выполнить ответ;
known_facts — единственные факты, которые можно утверждать;
unknown_facts — что остаётся неизвестным;
must_do — что обязательно сделать;
must_not_do — чего делать нельзя.

Выполни response_job, соблюдая must_do и must_not_do. Используй client_message и recent_dialogue для понимания реплики и продолжения разговора, но не извлекай из них новые свойства объектов и не повторяй последнюю реплику ассистента дословно.

Любое утверждение об объекте, варианте, месте, условиях или результате должно прямо подтверждаться known_facts. Не делай выводов из названий проектов, эмоциональных слов клиента, общего жизненного опыта или того, что кажется вероятным.

Факты можно перефразировать без изменения смысла. Нельзя усиливать, объединять, конкретизировать, причинно объяснять или переносить факт с одного варианта на другой. Не превращай название проекта в характеристику места. Не называй вариант тихим, удобным, выгодным, популярным или подходящим, если это прямо не указано в known_facts.

Не добавляй социальные обобщения: «это частый запрос», «обычно», «многим важно», «многие выбирают», «популярный вариант» и похожие фразы, если они явно не даны в known_facts.

Unknown_facts должны оставаться неизвестными. Если неизвестность важна, обозначь её коротко и естественно. Не используй служебные выражения: «данные», «контекст», «карточка», «сценарий», «жёсткие условия», «по проверенным данным».

Не выбирай, не исключай и не ранжируй варианты, если этого прямо не требуют response_job или must_do. Не предлагай оператора, менеджера или специалиста, если response_job не содержит offer_operator.

Пиши как живой, спокойный консультант. Сначала дай прямую реакцию или ответ, затем коротко добавь полезную мысль. Используй буквальные повседневные формулировки без рекламы, метафор и неестественных сочетаний вроде «спокойная цена».

Ответ должен быть коротким, желательно из 2–4 компактных абзацев. Можно задать не больше одного вопроса, только если он помогает выполнить response_job. Не используй markdown, списки, JSON, HTML, ссылки и телефоны.

Перед ответом молча проверь каждое качественное утверждение: найди его прямое основание в known_facts. Если основания нет, удали утверждение или замени его буквальной формулировкой известного факта. Самопроверку не показывай.

Верни только готовый текст для клиента, без пояснений и служебных пометок.
```

## Scenario matrix

| # | id | situation | status |
|---:|---|---|---|
| 1 | `human_blocks` | «человейники» / fear of dense high-rises | `success` |
| 2 | `choice_fatigue` | choice fatigue after many options | `success` |
| 3 | `spouse_contradiction` | contradictory spouse criteria | `success` |
| 4 | `distrust_pushed` | distrust / feeling pushed | `success` |
| 5 | `normal_to_live` | vague “там жить нормально?” | `transport_or_shape_failed` |
| 6 | `price_drop_fear` | price-drop fear | `not_run` |
| 7 | `direct_comparison` | direct comparison between two ЖК | `not_run` |
| 8 | `selected_missing_fact` | selected ЖК follow-up with missing fact | `not_run` |
| 9 | `financing_no_down_payment` | financing / no down payment | `not_run` |
| 10 | `typo_after_selection` | typo after selection | `not_run` |
| 11 | `request_operator` | request for operator | `not_run` |
| 12 | `budget_disappointment` | emotional budget disappointment | `not_run` |

## Prior-defect comparison summary

- Checks: no “частый запрос/многим”; no “двор между корпусами” from mere “окна во двор”; no inference that «Тихие липы» is quiet; no phrase “спокойная цена”.
- Prior targeted defects absent in all successful matching cases.

## Case results

### human_blocks — «человейники» / fear of dense high-rises

Synthetic: yes. Model: `gpt-5.2-chat`. Timeout: `180` seconds. Latency: `3516` ms.

Assignment:

```json
{
  "client_message": "Не хочу эти человейники, где окна в окна и куча машин. Есть вообще что-то нормальное?",
  "recent_dialogue": [
    {
      "role": "client",
      "text": "Смотрю двушку для жизни, не инвестицию."
    },
    {
      "role": "assistant",
      "text": "Могу подобрать варианты с понятной транспортной доступностью и спокойным двором."
    }
  ],
  "response_job": "calm_reframe_and_offer_next_filter",
  "known_facts": [
    "Все факты синтетические для диагностической пробы.",
    "Клиент ищет двухкомнатную квартиру для собственного проживания.",
    "Северный квартал: 19 этажей, закрытый двор без машин, окна части корпусов выходят во двор.",
    "Речной парк: 12 этажей, рядом набережная, открытая парковка по периметру.",
    "Нет подтверждённых фактов о плотности соседних домов, шуме и загруженности парковок."
  ],
  "unknown_facts": [
    "нет точных расстояний между корпусами",
    "нет данных о количестве машиномест"
  ],
  "must_do": [
    "признать опасение без спора",
    "не продавить выбор",
    "предложить фильтр по двору/этажности/окнам"
  ],
  "must_not_do": [
    "не называть ЖК малоэтажными",
    "не обещать отсутствие машин",
    "не говорить, что клиент преувеличивает"
  ]
}
```

Verbatim model response:

```text
Понимаю, о чём вы. Для жизни действительно хочется не только метраж, но и нормальное ощущение пространства вокруг.

Из того, что сейчас есть: в «Северном квартале» дома по 19 этажей, при этом двор закрыт для машин, а часть окон выходит во двор. В «Речном парке» — 12 этажей, рядом набережная, парковка открытая по периметру. При этом точных данных по расстояниям между корпусами и количеству машиномест у меня нет.

Можем дальше отфильтровать варианты по тому, что для вас критичнее: этажность, тип двора или чтобы окна не смотрели прямо друг в друга?
```

Mechanical checks:
- `api_shape_valid`: `True`
- `nonempty`: `True`
- `plain_text_no_json_fence_html`: `True`
- `max_one_question_mark`: `True`
- `no_banned_machine_phrases`: `True`
- `no_new_numbers`: `True`
- `no_new_project_names_outside_allowed_evidence`: `True`
- `no_links_or_phone`: `True`
- `no_unrequested_operator`: `True`
- `no_exact_replay_last_assistant`: `True`
- details: `{"new_numbers": [], "disallowed_projects": []}`

Prior-defect status:
- `social_generalization` avoided: `True`
- `yard_between_buildings_inference` avoided: `True`
- `tihie_lipy_quiet_inference` avoided: `True`
- `calm_price_phrase` avoided: `True`

Manual grounding map:
- claim: “Понимаю, о чём вы.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Для жизни действительно хочется не только метраж, но и нормальное ощущение пространства вокруг.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Из того, что сейчас есть: в «Северном квартале» дома по 19 этажей, при этом двор закрыт для машин, а часть окон выходит во двор.” | qualitative: `none` | basis: Северный квартал: 19 этажей, закрытый двор без машин, окна части корпусов выходят во двор.; Речной парк: 12 этажей, рядом набережная, открытая парковка по периметру. | status: `supported_or_non_object_claim`
- claim: “В «Речном парке» — 12 этажей, рядом набережная, парковка открытая по периметру.” | qualitative: `none` | basis: Северный квартал: 19 этажей, закрытый двор без машин, окна части корпусов выходят во двор.; Речной парк: 12 этажей, рядом набережная, открытая парковка по периметру. | status: `supported_or_non_object_claim`
- claim: “При этом точных данных по расстояниям между корпусами и количеству машиномест у меня нет.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Можем дальше отфильтровать варианты по тому, что для вас критичнее: этажность, тип двора или чтобы окна не смотрели прямо друг в друга?” | qualitative: `none` | basis: Северный квартал: 19 этажей, закрытый двор без машин, окна части корпусов выходят во двор. | status: `supported_or_non_object_claim`

Manual notes:
- Prior targeted defects absent in this response.
- No unsupported qualitative claim found by manual grounding map heuristic.

### choice_fatigue — choice fatigue after many options

Synthetic: yes. Model: `gpt-5.2-chat`. Timeout: `180` seconds. Latency: `2315` ms.

Assignment:

```json
{
  "client_message": "Я уже запуталась, вы мне столько всего накидали. Не понимаю, что смотреть.",
  "recent_dialogue": [
    {
      "role": "assistant",
      "text": "Ещё можно посмотреть Северный квартал, Речной парк и Тихие липы."
    }
  ],
  "response_job": "reduce_options_to_two_and_explain_why",
  "known_facts": [
    "Все факты синтетические для диагностической пробы.",
    "Клиенту показали три варианта.",
    "Северный квартал: ближе к метро, дороже остальных.",
    "Речной парк: спокойнее окружение, дольше ехать до центра.",
    "Тихие липы: дешевле, срок сдачи позже."
  ],
  "unknown_facts": [
    "что для клиента важнее: цена, дорога или срок"
  ],
  "must_do": [
    "снять перегруз",
    "оставить максимум два направления",
    "объяснить простыми словами"
  ],
  "must_not_do": [
    "не добавлять новые варианты",
    "не делать длинный список"
  ]
}
```

Verbatim model response:

```text
Тогда давайте сузим выбор до двух вариантов, чтобы было проще сравнить.

Если для вас важнее дорога и метро — смотрите Северный квартал. Он ближе к метро, но дороже остальных.

Если важнее цена — тогда Тихие липы. Этот вариант дешевле, но срок сдачи позже.

Речной парк пока можно отложить, чтобы не распыляться.
```

Mechanical checks:
- `api_shape_valid`: `True`
- `nonempty`: `True`
- `plain_text_no_json_fence_html`: `True`
- `max_one_question_mark`: `True`
- `no_banned_machine_phrases`: `True`
- `no_new_numbers`: `True`
- `no_new_project_names_outside_allowed_evidence`: `True`
- `no_links_or_phone`: `True`
- `no_unrequested_operator`: `True`
- `no_exact_replay_last_assistant`: `True`
- details: `{"new_numbers": [], "disallowed_projects": []}`

Prior-defect status:
- `social_generalization` avoided: `True`
- `yard_between_buildings_inference` avoided: `True`
- `tihie_lipy_quiet_inference` avoided: `True`
- `calm_price_phrase` avoided: `True`

Manual grounding map:
- claim: “Тогда давайте сузим выбор до двух вариантов, чтобы было проще сравнить.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Если для вас важнее дорога и метро — смотрите Северный квартал.” | qualitative: `none` | basis: Северный квартал: ближе к метро, дороже остальных. | status: `supported_or_non_object_claim`
- claim: “Он ближе к метро, но дороже остальных.” | qualitative: `none` | basis: Северный квартал: ближе к метро, дороже остальных. | status: `supported_or_non_object_claim`
- claim: “Если важнее цена — тогда Тихие липы.” | qualitative: `none` | basis: Тихие липы: дешевле, срок сдачи позже. | status: `supported_or_non_object_claim`
- claim: “Этот вариант дешевле, но срок сдачи позже.” | qualitative: `none` | basis: Клиенту показали три варианта.; Тихие липы: дешевле, срок сдачи позже. | status: `supported_or_non_object_claim`
- claim: “Речной парк пока можно отложить, чтобы не распыляться.” | qualitative: `none` | basis: Клиенту показали три варианта.; Речной парк: спокойнее окружение, дольше ехать до центра. | status: `supported_or_non_object_claim`

Manual notes:
- Prior targeted defects absent in this response.
- No unsupported qualitative claim found by manual grounding map heuristic.

### spouse_contradiction — contradictory spouse criteria

Synthetic: yes. Model: `gpt-5.2-chat`. Timeout: `180` seconds. Latency: `3477` ms.

Assignment:

```json
{
  "client_message": "Муж хочет ближе к центру, а я хочу тише и с зеленью. Мы уже спорим.",
  "recent_dialogue": [
    {
      "role": "assistant",
      "text": "Можно сравнить варианты по дороге и окружению."
    }
  ],
  "response_job": "mediate_criteria_without_taking_side",
  "known_facts": [
    "Все факты синтетические для диагностической пробы.",
    "Городские сады: ближе к центру, рядом крупная дорога.",
    "Речной парк: дальше от центра, рядом набережная.",
    "Нет подтверждённых фактов о тишине внутри квартир."
  ],
  "unknown_facts": [
    "готовы ли супруги к компромиссу по времени в дороге"
  ],
  "must_do": [
    "не становиться на сторону одного супруга",
    "предложить сравнить одну пару критериев"
  ],
  "must_not_do": [
    "не объявлять какой-то вариант лучшим для семьи"
  ]
}
```

Verbatim model response:

```text
Похоже, у вас сейчас два разных приоритета, и оба понятны. По тем вариантам, которые вы смотрите, разница как раз в расположении: «Городские сады» ближе к центру и рядом крупная дорога, а «Речной парк» дальше от центра, рядом набережная.

Чтобы спор не крутился вокруг общих ощущений, можно сравнить только одну пару критериев: сколько времени каждый готов тратить на дорогу и насколько важна близость к набережной или центру в повседневной жизни. Про тишину внутри квартир подтверждённых данных пока нет, поэтому лучше не делать на этом окончательных выводов заранее.
```

Mechanical checks:
- `api_shape_valid`: `True`
- `nonempty`: `True`
- `plain_text_no_json_fence_html`: `True`
- `max_one_question_mark`: `True`
- `no_banned_machine_phrases`: `True`
- `no_new_numbers`: `True`
- `no_new_project_names_outside_allowed_evidence`: `True`
- `no_links_or_phone`: `True`
- `no_unrequested_operator`: `True`
- `no_exact_replay_last_assistant`: `True`
- details: `{"new_numbers": [], "disallowed_projects": []}`

Prior-defect status:
- `social_generalization` avoided: `True`
- `yard_between_buildings_inference` avoided: `True`
- `tihie_lipy_quiet_inference` avoided: `True`
- `calm_price_phrase` avoided: `True`

Manual grounding map:
- claim: “Похоже, у вас сейчас два разных приоритета, и оба понятны.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “По тем вариантам, которые вы смотрите, разница как раз в расположении: «Городские сады» ближе к центру и рядом крупная дорога, а «Речной парк» дальше от центра, рядом набережная.” | qualitative: `none` | basis: Городские сады: ближе к центру, рядом крупная дорога.; Речной парк: дальше от центра, рядом набережная. | status: `supported_or_non_object_claim`
- claim: “Чтобы спор не крутился вокруг общих ощущений, можно сравнить только одну пару критериев: сколько времени каждый готов тратить на дорогу и насколько важна близость к набережной или центру в повседневной жизни.” | qualitative: `none` | basis: Городские сады: ближе к центру, рядом крупная дорога. | status: `supported_or_non_object_claim`
- claim: “Про тишину внутри квартир подтверждённых данных пока нет, поэтому лучше не делать на этом окончательных выводов заранее.” | qualitative: `none` | basis: Нет подтверждённых фактов о тишине внутри квартир. | status: `supported_or_non_object_claim`

Manual notes:
- Prior targeted defects absent in this response.
- No unsupported qualitative claim found by manual grounding map heuristic.

### distrust_pushed — distrust / feeling pushed

Synthetic: yes. Model: `gpt-5.2-chat`. Timeout: `180` seconds. Latency: `2593` ms.

Assignment:

```json
{
  "client_message": "Такое ощущение, что вы меня просто подталкиваете к Северному кварталу.",
  "recent_dialogue": [
    {
      "role": "assistant",
      "text": "Северный квартал выглядит самым подходящим, лучше его не упустить."
    }
  ],
  "response_job": "repair_trust_and_offer_neutral_comparison",
  "known_facts": [
    "Все факты синтетические для диагностической пробы.",
    "Северный квартал подходит по бюджету клиента.",
    "Есть другие варианты в подборке, но их детали в assignment не переданы."
  ],
  "unknown_facts": [
    "какие альтернативы клиент хочет сравнить"
  ],
  "must_do": [
    "извиниться за давление",
    "вернуть клиенту контроль выбора",
    "предложить нейтральное сравнение"
  ],
  "must_not_do": [
    "не защищаться",
    "не продолжать продавливание",
    "не говорить что вариант лучший"
  ]
}
```

Verbatim model response:

```text
Понимаю, почему так могло прозвучать. Извините, не хотел давить на выбор.

Решение, конечно, за вами. Я отметил Северный квартал только потому, что он проходит по вашему бюджету, но это не означает, что нужно останавливаться именно на нём.

Можем спокойно сравнить его с другими вариантами из подборки по тем параметрам, которые для вас важны. Что хотите поставить в приоритет при сравнении?
```

Mechanical checks:
- `api_shape_valid`: `True`
- `nonempty`: `True`
- `plain_text_no_json_fence_html`: `True`
- `max_one_question_mark`: `True`
- `no_banned_machine_phrases`: `True`
- `no_new_numbers`: `True`
- `no_new_project_names_outside_allowed_evidence`: `True`
- `no_links_or_phone`: `True`
- `no_unrequested_operator`: `True`
- `no_exact_replay_last_assistant`: `True`
- details: `{"new_numbers": [], "disallowed_projects": []}`

Prior-defect status:
- `social_generalization` avoided: `True`
- `yard_between_buildings_inference` avoided: `True`
- `tihie_lipy_quiet_inference` avoided: `True`
- `calm_price_phrase` avoided: `True`

Manual grounding map:
- claim: “Понимаю, почему так могло прозвучать.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Извините, не хотел давить на выбор.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Решение, конечно, за вами.” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`
- claim: “Я отметил Северный квартал только потому, что он проходит по вашему бюджету, но это не означает, что нужно останавливаться именно на нём.” | qualitative: `none` | basis: Северный квартал подходит по бюджету клиента. | status: `supported_or_non_object_claim`
- claim: “Можем спокойно сравнить его с другими вариантами из подборки по тем параметрам, которые для вас важны.” | qualitative: `quiet_or_calm` | basis: none | status: `unsupported_qualitative_claim`
- claim: “Что хотите поставить в приоритет при сравнении?” | qualitative: `none` | basis: none | status: `supported_or_non_object_claim`

Manual notes:
- Prior targeted defects absent in this response.
- Unsupported qualitative/inference claims flagged: 1

### normal_to_live — vague “там жить нормально?”

Synthetic: yes. Model: `gpt-5.2-chat`. Timeout: `180` seconds. Latency: `None` ms.

Assignment:

```json
{
  "client_message": "А в Речном парке там жить нормально вообще?",
  "recent_dialogue": [
    {
      "role": "assistant",
      "text": "Речной парк можно рассмотреть как более спокойный вариант."
    }
  ],
  "response_job": "answer_vague_liveability_with_known_facts_and_limits",
  "known_facts": [
    "Все факты синтетические для диагностической пробы.",
    "Речной парк: рядом набережная.",
    "Речной парк: до центра ехать дольше, чем из Городских садов.",
    "Речной парк: открытая парковка по периметру.",
    "Нет подтверждённых отзывов жителей, замеров шума или данных о школах."
  ],
  "unknown_facts": [
    "что именно клиент вкладывает в нормально"
  ],
  "must_do": [
    "ответить без категоричного да/нет",
    "назвать известные плюсы и ограничения"
  ],
  "must_not_do": [
    "не обещать комфорт",
    "не ссылаться на отзывы"
  ]
}
```

Verbatim model response:

```text

```

Mechanical checks:
- `api_shape_valid`: `False`
- `nonempty`: `False`
- details: `{"error_message": "HTTP 504: <html>\r\n<head><title>504 Gateway Time-out</title></head>\r\n<body>\r\n<center><h1>504 Gateway Time-out</h1></center>\r\n<hr><center>openresty</center>\r\n</body>\r\n</html>"}`

Prior-defect status:
- not applicable because no model text

Manual grounding map:
- none

Manual notes:
- Stopped at first subsequent API/transport error.

## Cross-case patterns

- Route produced 4 shaped nonempty response(s) before any stop condition.
- Mechanical failures among successful cases: none.
- Unsupported qualitative/manual grounding flags: `distrust_pushed`.
- Prior targeted defects status: absent in successful cases.

## Post-read verification correction

The initial mechanical/manual summary above was too permissive. A direct reread of the verbatim outputs found three prompt-contract violations and one false-positive flag:

- `human_blocks`: the phrase “точных данных” violates the prompt's explicit ban on the service word “данные”.
- `spouse_contradiction`: “подтверждённых данных” violates the same explicit ban.
- `distrust_pushed`: “Я отметил Северный квартал только потому, что он проходит по вашему бюджету” invents a sole causal explanation for the previous recommendation. The assignment confirms budget fit, but does not say it was the only reason.
- `distrust_pushed`: “Можем спокойно сравнить” is conversational manner, not a claim that an object is quiet. The earlier `quiet_or_calm` grounding flag for this phrase is a false positive.

Therefore, all four responses are API/shape successes, but only `choice_fatigue` passes the complete post-read prompt-contract check without a noted violation. The four targeted baseline defects were absent, but the probe exposed gaps in morphological banned-word checks and causal-grounding review.

## Cautious verdict

Probe partially completed. Use only the preserved successful verbatim outputs and transport failure record; do not generalize because full 12-case coverage was not reached.

## Successful case IDs

- `human_blocks`
- `choice_fatigue`
- `spouse_contradiction`
- `distrust_pushed`
