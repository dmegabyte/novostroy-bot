# NMBOT scenario model probe — 2026-07-21

## Цель

Проверить `google/gemini-2.5-flash` на двух известных scenario overlays и двух
понятных вопросах без готового recipe. Все вызовы read-only; production runtime
и VPS не изменялись.

## Контракт проверки

- known scenarios: `scripts/nmbot_v2_quality_gate.py --live`;
- unknown scenarios: `scripts/nmbot_v2_open_question_probe.py`;
- semantic validator advisory-only: warnings сохраняются, но распарсенный ответ
  не заменяется fallback-ом;
- fallback остаётся только для ответа, который нельзя распарсить как JSON object,
  или для transport/provider failure.

## Исправления перед финальным прогоном

1. Quality harness переведён с `build_response_plan()` на
   `build_final_response_plan()`, чтобы recipe выбирался до `ResponseBrief`.
2. Numeric MCP flags `1/0` нормализуются как boolean infrastructure evidence.
3. Composer prompt разделяет обязательные identity facts и scenario anchor.
4. Investment prompt запрещает выводить популярность/спрос/интерес арендаторов
   из `ads_count`, паркинга, экологии или инфраструктуры.
5. Semantic validation переведена в warnings; parser принимает лишние и
   неполные option fields, которые можно безопасно собрать.
6. Добавлен additive open-question brief contract и read-only probe.

## Результаты

### 1. Known scenario: family

Команда:

```bash
python3 scripts/nmbot_v2_quality_gate.py --case family --live --timeout 90
```

Результат:

```text
status: primary
score: 10/10
hard blockers: none
facts: 3
missing: 0
```

Модель показала три ЖК, сохранила exact location/price и использовала разные
family anchors: школа/сад, готовность, семейный бюджет.

### 2. Known scenario: investment

Команда:

```bash
python3 scripts/nmbot_v2_quality_gate.py --case investment --live --timeout 90
```

Результат:

```text
status: primary
score: 10/10
hard blockers: none
advisory warning: unknown_number_or_sensitive_claim
```

Warning не изменил клиентский ответ. Модель использовала цену как порог входа,
готовность как отсутствие ожидания стройки и `ads_count` только как буквальный
счётчик предложений.

### 3. Unknown scenario, answer available

Вопрос:

```text
Кто застройщик у Бусиновского парка?
```

Команда:

```bash
python3 scripts/nmbot_v2_open_question_probe.py --case answerable --timeout 90
```

Ответ:

```text
Застройщик «Бусиновского парка» — ПИК

Что ещё проверить по этому ЖК?
```

Результат: `primary`, errors/warnings отсутствуют. Модель не подставила
family/life/investment шаблон.

### 4. Unknown scenario, fact missing

Вопрос:

```text
А зимой там сильно дует между домами?
```

Команда:

```bash
python3 scripts/nmbot_v2_open_question_probe.py --case missing --timeout 90
```

Ответ:

```text
К сожалению, у меня нет информации о ветровой обстановке между корпусами в ЖК Бусиновский парк

Такие детали лучше уточнить у застройщика или на месте

Хотите проверить другой вопрос по этому ЖК?
```

Этот результат признан нарушением: ответ отправлял клиента уточнять сведения у
застройщика или на месте и не завершался запросом номера. Corrected contract:
`operator_phone_request`.

Исправленный повторный ответ:

```text
Я не могу сейчас посмотреть информацию про ветровую обстановку между корпусами

Точный ответ по ветровой обстановке уточнит оператор.

Подскажите номер телефона, чтобы оператор уточнил это по ЖК «Бусиновский парк»?
```

Результат: `primary`, errors/warnings отсутствуют. Ответ ведёт только к оператору
и заканчивается запросом номера телефона.

## Вывод

Единый JSON работает и с известным viewpoint overlay, и с нейтральным открытым
вопросом. Scenario recipe улучшает приоритеты и пользу, но его отсутствие не
лишает Ирину возможности дать прямой grounded answer или честную boundary.

Это локальный model probe, а не production/Jivo release evidence.

## Локальная regression-проверка

```text
pytest -q
747 passed, 656 existing aiohttp warnings
```
