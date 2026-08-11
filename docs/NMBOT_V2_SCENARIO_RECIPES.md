# NMBOT V2 Scenario Recipes

Реестр определяет **как** строится ответ и какие продолжения диалога допустимы.
Planner-модель возвращает только semantic meaning: выбранный canonical ЖК,
subject/facts, viewpoint, отношение к домену и outcome закрытого вопроса.
Runtime exact-валидирует эти значения, выводит технические stage/action/scope и
ровно один раз выбирает executable recipe из `nmbot_v2/scenario_recipes.py`.
Один и тот же resolved recipe прикрепляется к `ResponsePlan`, используется
детерминированным fallback и передаётся answer-модели через `ResponseBrief`.
Answer-модель не выбирает route, recipe, anchor, scope или CTA: она только
формулирует разрешённые подтверждённые данные.

## Общий контракт рецепта

Каждый рецепт содержит:

- `when` — детерминированные условия выбора кода;
- `fact_priority` — порядок выбора единственного главного факта;
- `structure` — порядок блоков ответа;
- `allowed_benefit` — разрешённый вывод из факта;
- `forbidden` — выводы, которые нельзя делать;
- `fallback` — следующий рецепт или честная формулировка при отсутствии поля;
- `cta` — единственный следующий шаг.

Если CTA задаёт закрытый вопрос, рецепт дополнительно содержит
`reply_contract`:

- `allowed_outcomes` — ограниченный набор смыслов следующей реплики;
- `transition_recipes` — готовая инструкция/рецепт ответа для каждого смысла;
- `planner_context` — краткое описание уже предложенного действия, scope и
  выбранного ЖК либо текущего списка.

Так следующий ход не начинается с нуля: planner получает уже ожидаемые смыслы
ответа и выбирает один из них, а answer-модель получает заранее подготовленную
инструкцию. Если ни один смысл не подходит, planner возвращает `unexpected`;
ему соответствует отдельный безопасный recipe уточнения, а не самовольный
контактный сбор или новый поиск.

Общие правила для всех рецептов:

- использовать только поля `OptionCard` из `ResponseBrief`;
- не обещать наличие, бронь, ставку, одобрение, доходность или звонок;
- не смешивать несколько ЖК в один блок;
- не придумывать сравнительное преимущество: «отличается» допустимо только,
  если код выбрал разный факт-акцент у карточек;
- финальный вопрос один; когда вопрос не нужен, использовать один короткий
  CTA без вопросительного знака.

## 1. Shortlist для жизни

### `life_shortlist`

- **when:** `stage=first_list|refinement|current_options`, `viewpoint=life`,
  в ответе 1–3 canonical cards.
- **intro:** назвать только реально применённые параметры: цель жизни,
  бюджет/район/готовность, если они есть в `state_delta_summary` или в
  подтверждённых карточках. Не писать «учла изменение», если изменения не было.
- **fact_priority per card:** `metro` → `ready` → `finishing` → `price` →
  `infrastructure` → `location`.
- **structure per card:** название + 1–3 headline facts → один выбранный факт
  → его разрешённая бытовая польза.
- **allowed_benefit:**
  - `metro` → проще планировать ежедневные поездки;
  - `ready=сдан|готов` → можно рассматривать готовый формат без ожидания
    окончания строительства;
  - `ready` со сроком → понятен горизонт ожидания;
  - `finishing` → не нужно начинать с чернового ремонта;
  - `price` → понятен стартовый ориентир для бюджета;
  - `infrastructure` → удобнее решать повседневные задачи, только называя
    реально пришедший объект инфраструктуры;
  - `location` → можно сопоставить ежедневный маршрут с другими вариантами.
- **distinctness:** код назначает карточкам разные доступные акценты по
  приоритету; повторять один акцент разрешено только если иных подтверждённых
  полей нет.
- **fallback:** если у карточки нет поля для пользы — сообщить только
  подтверждённые headline facts и предложить открыть ЖК подробнее.
- **cta:** `Какой вариант хотите рассмотреть подробнее?`

### `family_shortlist`

- **when:** тот же stage, `viewpoint=family`.
- **fact_priority per card:** школы/сады → двор без машин/безопасность →
  парк/вода/спорт → метро → `ready` → `finishing` → `price`.
- **allowed_benefit:**
  - школы/сады → проще ежедневная логистика с ребёнком;
  - двор без машин/безопасность → спокойнее ежедневный сценарий;
  - парк/вода/спорт → понятнее прогулки после учёбы и в выходные;
  - метро → проще семейные маршруты;
  - готовность/отделка/цена → только второй слой, если семейных фактов нет.
- **forbidden:** нельзя называть вариант семейным из цены, отделки или класса,
  пока у карточки есть более сильный семейный факт.
- **fallback / cta:** как `life_shortlist`.

### `investment_shortlist`

- **when:** shortlist, `viewpoint=investment`.
- **fact_priority per card:** `sales_count` → подтверждённые finance/discount
  terms → `price` → `ready` → `finishing` → `metro|location`.
- **allowed_benefit:**
  - `sales_count` → фактический ориентир для сравнения без прогноза;
  - finance/discount → понятнее условия входа, только по пришедшим цифрам;
  - цена → понятен порог входа;
  - готовность → понятен горизонт ожидания;
  - отделка → меньше стартовых работ после покупки.
- **forbidden:** доходность, окупаемость, рост цены, ликвидность, «спрос» по
  `ads_count`; `ads_count` можно называть только количеством объявлений.
- **cta:** `Какой вариант разберём по цене и условиям входа?`

### `rental_shortlist`

- **when:** shortlist, `viewpoint=rental`.
- **fact_priority per card:** компактный `rooms|area` → `finishing` → `metro`
  → `ready` → `location` → подтверждённый `sales_count`.
- **allowed_benefit:** компактный формат — проще сопоставить с бюджетом;
  отделка — меньше подготовки после покупки; метро — понятнее ежедневный
  маршрут; готовность — понятен срок старта.
- **forbidden:** ставка аренды, окупаемость, обещание «быстро сдать»;
  `ads_count` не означает спрос.
- **cta:** `Какой вариант посмотреть по формату и готовности?`

## 2. Работа с уже показанным списком

### `refined_shortlist`

- **when:** `stage=refinement`, есть новые подтверждённые параметры.
- **structure:** коротко назвать только изменённые условия → показать 1–3
  карточки тем же рецептом соответствующего viewpoint → один CTA.
- **forbidden:** не говорить, что учтены параметры, для которых в данных нет
  evidence; не начинать диалог заново.
- **fallback:** если точных карточек нет, передать в `near_or_no_results`.
- **cta:** `Какой вариант открыть подробнее?`

### `repeat_shortlist`

- **when:** новый поиск после запроса «другие/похожие», карточки не совпадают с
  предыдущим visible list.
- **structure:** коротко подтвердить, что показаны другие варианты → применить
  рецепт текущего viewpoint с разными факт-акцентами.
- **forbidden:** повторять прежние ЖК без явной причины; называть новый ЖК
  «лучше» без подтверждённого отличающегося факта.
- **cta:** `Какой из новых вариантов разобрать подробнее?`

### `current_options_comparison`

- **when:** `stage=current_options`, клиент спрашивает о всех текущих ЖК, но
  не выбрал один.
- **structure:** назвать ось сравнения из `response_viewpoint`/вопроса → по
  одному подтверждённому факту на каждый ЖК → один вопрос выбора.
- **fact_priority:** использовать только поля, относящиеся к оси: для жизни —
  рецепты life; для семьи — family; для финансирования — только пришедшие
  финансовые условия или честная граница данных.
- **forbidden:** не превращать сравнение в новый поиск и не повторять полный
  первоначальный текст каждой карточки.
- **cta:** `Какой ЖК рассмотреть предметно?`

### `why_this_shortlist`

- **when:** клиент спрашивает, почему показаны эти ЖК / как происходил выбор.
- **structure:** назвать 2–3 подтверждённых критерия текущего подбора → у
  каждого ЖК назвать ровно один факт, который попал в критерий → предложить
  сравнение по одной понятной оси.
- **forbidden:** алгоритм, модель, MCP, внутренние правила, незафиксированные
  критерии.
- **fallback:** если объясняющего поля нет, честно сказать, что его нет в
  сохранённых данных и предложить проверить конкретный параметр.
- **cta:** `Сравнить их по бюджету, дороге или готовности?`

## 3. Выбранный ЖК

### `selected_life_ready`

- **when:** `stage=selected_object`, `scope=one`, viewpoint `life|family`,
  выбранная карточка имеет `ready=сдан|готов`.
- **anchor:** использовать только `ready` как главный факт.
- **structure:** назвать выбранный ЖК → «дом сдан/готов» → разрешённая польза
  «можно рассматривать готовый формат без ожидания окончания строительства»
  → 1–2 дополнительных headline facts, если они есть.
- **forbidden:** не утверждать, что квартира есть в наличии, доступна бронь или
  ключи выдают прямо сейчас.
- **fallback:** если `ready` отсутствует, resolver выбирает следующий рецепт
  `selected_life_metro`, `selected_life_finishing`, `selected_life_price` или
  `selected_life_location`, а не просит модель придумать причину.
- **cta:** `Проверить по нему актуальные квартиры, площадь или возможность брони?`

### `selected_life_metro`

- **when:** выбран один ЖК, `metro` подтверждено, но нет готовности.
- **anchor / benefit:** `metro` → проще оценить ежедневный маршрут.
- **forbidden:** не переводить поле метро в обещание времени пути, если минут
  в карточке нет.
- **cta:** как `selected_life_ready`.

### `selected_life_finishing`

- **when:** выбран один ЖК, подтверждена отделка, сильнее доступных предыдущих
  anchors.
- **anchor / benefit:** `finishing` → не нужно начинать с чернового ремонта.
- **cta:** как `selected_life_ready`.

### `selected_life_price`

- **when:** выбран один ЖК, есть цена, но нет более сильного anchor.
- **anchor / benefit:** стартовая цена → понятен ориентир относительно бюджета.
- **forbidden:** не называть ЖК доступным/выгодным, если сравнение бюджета не
  подтверждено параметрами запроса.
- **cta:** как `selected_life_ready`.

### `selected_life_location`

- **when:** выбран один ЖК, есть `location`, но нет остальных anchors.
- **anchor / benefit:** район/локация → есть конкретная точка для оценки
  ежедневного маршрута.
- **cta:** как `selected_life_ready`.

### `selected_details`

- **when:** клиент просит подробнее о уже выбранном ЖК.
- **structure:** краткий вывод по одному recipe-anchor → 2–4 наиболее полезных
  подтверждённых поля, сгруппированных по жизни/транспорту/готовности → один
  CTA.
- **forbidden:** «досье», «карточка», внутренний поиск; недостающие детали
  нельзя заполнять предположением.
- **fallback:** этот recipe выбирается, когда клиент просит рассказать подробнее,
  но в выбранной карточке нет готового recipe-anchor. Это не запрос конкретного
  отсутствующего поля: такой запрос остаётся в `selected_fact_not_confirmed`.
- **cta:** `Хотите сравнить его с другим ЖК или проверить актуальное наличие?`

### `fact_confirmed` / `fact_not_confirmed`

- **when:** вопрос о конкретном поле выбранного ЖК или текущего списка.
- **structure:** первым словом статус «подтверждено» / «в данных нет
  подтверждения» → один факт-доказательство либо честная граница → один CTA.
- **forbidden:** вероятностные догадки («скорее всего»), перенос факта одного
  ЖК на другой, широкий shortlist.
- **cta:** `Проверить другой параметр по этому ЖК?` либо предложение человека
  только если live-деталь действительно нужна.

### Selected-object fact recipes

Resolver выбирает один из трёх закрытых вариантов только для канонического
выбранного ЖК:

- **`selected_fact_confirmed`:** запрошенный static fact уже есть в structured
  card. Ответ называет один подтверждённый факт и его практический смысл; MCP
  повторно не вызывается.
- **`selected_fact_not_confirmed`:** нужного поля нет. Ответ честно говорит,
  что подтверждения нет, не показывает другие ЖК и предлагает точечную
  проверку только этого параметра.
- **`selected_live_fact_check`:** dynamic fact (`parking_price`,
  `parking_inventory`, `apartment_inventory`, `mortgage_terms`) требует exact
  refresh. Запрос использует каноническое имя, `count=1` и bounded
  `facts_needed`. Успешно применённый результат отмечает fact transient-полем
  `fresh_facts`; timeout/mismatch сохраняет cached card и честную границу.

`selected_live_fact_check` открывает закрытый reply contract
`selected_live_fact_consent` только если dynamic fact не подтверждён свежим
exact enrichment: `accept → operator_handoff_name_capture`,
`decline → selected_live_fact_declined`, `ask_or_clarify →
selected_live_fact_consent_clarification`, `unexpected|invalid|missing →
selected_live_fact_consent_recovery`. До `accept` имя и телефон не
запрашиваются. Fresh dynamic fact отвечает сразу и не открывает handoff.

`DialogFocus` хранит subject, semantic intent и названия запрошенных/отвеченных
fields, но не значения и не evidence. Предыдущий ответ и focus разрешают
эллиптическое продолжение (`паркинг` → `А сколько стоит?` → `parking_price`),
однако факты берутся только из structured card/exact enrichment.

Для паркинга поля разделены строго:

- `parking` — проектный признак наличия паркинга;
- `parking_price` — цена машиноместа;
- `parking_inventory` — актуальное наличие машиномест.

Подтверждённый `parking` не доказывает цену или наличие. Ни один selected fact
recipe не может расширить `scope=one`, запустить broad search или показать
карточки других ЖК.

## 4. Финансирование и оператор

### `selected_financing`

- **when:** `stage=financing_clarification`, выбран один ЖК.
- **structure:** потребность по выбранному ЖК → спокойная граница известных
  условий → почему проверка этого объекта предметна → предложение проверки →
  один CTA.
- **forbidden:** начинать с «в данных нет»; ставка, первоначальный взнос,
  аккредитация, одобрение, наличие, срок обратной связи; просьба назвать себя
  или оставить телефон до согласия на проверку.
- **cta:** `Проверить условия по этому ЖК?`
- **reply_contract:**
  - `allowed_outcomes:` `accept`, `decline`, `ask_or_clarify`, `unexpected`;
  - `planner_context:` проверка ипотечных условий выбранного ЖК, `scope=one`;
  - `transition_recipes:` `accept → operator_handoff_name_capture`,
    `decline → financing_declined`,
    `ask_or_clarify → financing_consent_clarification`,
    `unexpected → financing_consent_recovery`.

### `current_options_financing`

- **when:** financing по всем текущим ЖК, один не выбран.
- **structure:** признать запрос по всему текущему списку → честная граница
  условий → ценность отдельной проверки каждого ЖК → предложение проверки →
  один CTA.
- **forbidden:** просить выбрать ЖК; начинать с отсутствия данных; ставка,
  первоначальный взнос, аккредитация, одобрение, наличие, срок обратной связи;
  просьба назвать себя или оставить телефон до согласия.
- **cta:** `Проверить условия по всем этим ЖК?`
- **reply_contract:**
  - `allowed_outcomes:` `accept`, `decline`, `ask_or_clarify`, `unexpected`;
  - `planner_context:` проверка условий по каждому ЖК текущего списка,
    `scope=all`;
  - `transition_recipes:` `accept → operator_handoff_name_capture`,
    `decline → financing_declined`,
    `ask_or_clarify → financing_consent_clarification`,
    `unexpected → financing_consent_recovery`.

### `operator_consent`

- **when:** клиент отвечает на уже опубликованное предложение подключить
  менеджера; это отдельный consent-turn, а не новый поиск и не выбор ЖК.
- **planner context:** `offered_action=collect_contact_phone`; предметом
  остаётся сохранённый текущий контекст, а не автоматически весь новый список.
- **allowed_outcomes:** `accept`, `decline`, `ask_or_clarify`, `unexpected`.
- **transitions:** `accept → operator_handoff_phone_capture`,
  `decline → selected_live_fact_declined`,
  `ask_or_clarify|unexpected → operator_handoff_name_capture` с сохранением
  `operator_consent` pending.
- **forbidden:** запуск нового поиска, молчаливый выбор ЖК, запрос телефона до
  принятого согласия и подмена отсутствующего evidence утверждением об отсутствии
  квартир.

### Продолжения `financing`-offer

- **`operator_handoff_name_capture`:** только после planner outcome `accept`.
  Сценарий просит, как обращаться к клиенту; затем использует защищённую
  phone-first воронку. До `accept` имя и номер не запрашиваются.
- **`financing_declined`:** коротко принимает отказ, не повторяет CTA и
  предлагает нейтральный следующий шаг по выбранному ЖК или текущему списку.
- **`financing_consent_clarification`:** отвечает только на заданное уточнение
  подтверждёнными фактами и повторяет исходный CTA ровно один раз.
- **`financing_consent_recovery`:** кратко объясняет, какое действие было
  предложено, и повторяет исходный CTA. Не запускает контактную воронку и не
  меняет scope.

### `operator_handoff`

- **when:** `stage=operator_handoff`, клиент просит live-деталь или данных
  объективно недостаточно.
- **structure:** назвать ровно тот контекст, который уже подтверждён (ЖК,
  вопрос, при наличии район/цена) → назвать непроверяемую live-деталь →
  предложить проверку человеком.
- **forbidden:** наличие, этаж, корпус, бронь, срок звонка; оператор вместо
  полезного ответа.
- **cta:** `Как к вам обращаться?`

## 5. Границы и отсутствие результата

### `near_or_no_results`

- **when:** точных `facts` нет; есть `near` либо нет карточек вовсе.
- **structure with near:** честно назвать, что варианты ближайшие, затем для
  каждого показать `why_close` как отличие, не как точное совпадение.
- **structure without cards:** назвать неподтверждённый/слишком жёсткий
  параметр и предложить ослабить ровно один параметр.
- **forbidden:** выдуманные альтернативы; «ничего нет» без следующего шага.
- **cta:** `Ослабим [один конкретный параметр]?`

### `default_clarification`

- **when:** нет достаточного смысла для поиска или сценария.
- **structure:** одна спокойная фраза о необходимости ориентира → один вопрос
  по самой ценной недостающей оси: локация, бюджет, комнатность или цель.
- **forbidden:** поиск, карточки, два вопроса, предположение о цели клиента.
- **cta:** сам уточняющий вопрос.

### `off_topic`

- **when:** запрос не о новостройках Москвы/МО.
- **structure:** короткая доброжелательная граница → возврат к подбору → один
  вопрос.
- **forbidden:** ЖК, поиск, техническое объяснение системы.
- **cta:** `Вернёмся к подбору квартиры?`
- **state:** search/enrichment/operator запрещены; текущие cards, selected ЖК и
  параметры сохраняются, старый pending offer очищается.

## 6. Правила выбора и композиции

Executable resolver работает детерминированно и вызывается один раз на ход:

1. `off_topic` заменяет любой старый pending offer.
2. Валидированный pending reply contract выбирает continuation recipe.
3. Затем применяются operator и financing replacement-рецепты.
4. Selected fact выбирается по статусу confirmed / not confirmed / live check.
5. Для обычного selected ЖК применяется цепочка ready → metro → finishing →
   price → location → details.
6. Затем обрабатываются near/no-results, refinement, viewpoint shortlist,
   current-options и безопасный `default_clarification`.
7. Для каждой карточки выбирается один доступный anchor по `fact_priority` и
   резервирует его, чтобы карточки не повторяли одну пользу без необходимости.
8. `selected_financing` и `operator_handoff` заменяют обычный selected intro;
   они не склеиваются с ним как две независимые инструкции.
9. Недоступное поле не передаётся модели как пустой повод для рассуждения:
   resolver выбирает fallback recipe или честную границу данных.

## 7. Что должно попасть в composer payload

`ResponsePlan` получает готовый contract один раз: `recipe_id`, per-card
`recipe_cards`, `anchor_fact`, `allowed_benefit`, `forbidden_inferences`,
`cta_template`, `composition_mode`, `reply_contract_id`. `ResponseBrief`
переносит тот же contract в composer payload. Модель получает canonical cards
и формулирует строгий JSON, но не меняет recipe, порядок карточек, scope, факты,
anchors или CTA. Для следующего хода runtime сохраняет typed `last_offer`; модель
разрешает короткий follow-up через его `subject_name` и `action`.

## 8. Исполняемая матрица

Runtime registry в `nmbot_v2/scenario_recipes.py` сейчас содержит 30
исполняемых recipe entries. В нём три закрытых reply contract:
`financing_consent`, `selected_live_fact_consent` и `operator_consent`. Все три принимают только
outcome `accept`, `decline`, `ask_or_clarify`, `unexpected`; invalid/missing
не считается согласием и уходит в recovery.

`tests/test_nmbot_v2_recipe_transition_matrix.py` проверяет registry references,
приоритет replacement-рецептов, viewpoint anchors, selected static/dynamic
facts, fresh/timeout/mismatch, financing/live-fact outcomes, off-topic,
`apartment_inventory` и равенство recipe metadata между fallback и composer.
Полный локальный gate после внедрения реестра: **551 passed**, 473 уже
существующих `aiohttp NotAppKeyWarning`, 3.54 sec.

Продовый deploy этой партии выполнен из backup `backups/deploy-20260720-121023`:
`novostroy-bot-api.service` active, remote hashes deployed-файлов совпали.
Fresh live smoke подтвердил только клиентский Jivo/API путь: off-topic вернул
ограниченную фразу с возвратом к недвижимости; selected live fact по паркингу
сохранил `scope=one` и честную границу отсутствующих данных; короткое
продолжение про цену после этого разрешилось как `parking_price`. Этот smoke не
доказывает доставку в Google Sheet. Исторически отдельно подтверждённый путь
selected mortgage callback → Sheet остаётся отдельным фактом, не частью этого
smoke.

## Источники

- `docs/IDEAL_IRINA_UX.md:20-45, 63-189`;
- `docs/CARD_PRESENTATION_RULE.md:11-49`;
- `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md:54-100`;
- legacy-библиотека приёмов: `prompts/scenarios/*.txt`.
