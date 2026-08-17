# Уровни тестирования V6

Это единая карта проверок V6. Каждый уровень отвечает только за свой слой.
Зелёный Prompt1 или Prompt2 не означает, что весь бот или Jivo-контур работает.

| Уровень | Владелец | Вход | Модель? | MCP? | VPS/Jivo? | Что доказывает | Чего не доказывает | Команда |
|---|---|---|---|---|---|---|---|---|
| `prompt1` | Prompt1 | synthetic JSON без tool-вызова | Да | Нет | Нет | решение `continue/clarify/request_phone` и JSON-контракт реальной модели | MCP, runtime, релиз и Jivo | `python3 scripts/nmbot_v6_test_layers.py --layer prompt1 --execute --confirm-model` |
| `prompt2` | Prompt2 | готовый synthetic material | Да | Нет | Нет | `response/final_question`, grounding и приоритеты реальной модели | router/state, MCP, релиз и Jivo | `python3 scripts/nmbot_v6_test_layers.py --layer prompt2 --execute --confirm-model` |
| `runtime` | V6 runtime | focused pytest с fake/stub моделями | Нет | Нет | Нет | parser, state, URL, телефон и маршрутизацию | реальный ответ модели, MCP и Jivo | `python3 scripts/nmbot_v6_test_layers.py --layer runtime --execute` |
| `contour` | release/bridge | TEST-only Jivo smoke | Да | Косвенно | Да | работу опубликованного TEST-релиза через bridge и терминальное событие | полноту всех бизнес-сценариев | `python3 scripts/nmbot_v6_test_layers.py --layer contour --execute --confirm-live` |

`--list` показывает эту карту в JSON. Без `--execute` runner ничего не запускает:
он только печатает точную команду и границу доказательства.

## Как выбрать уровень

- Меняется формулировка или payload Prompt1 — запускай `prompt1`.
- Меняется ответ или `final_question` Prompt2 — запускай `prompt2`.
- Меняется state, parser, URL, телефон или router — запускай `runtime`.
- Нужно доказать TEST-релиз, bridge или доставку Jivo — запускай `contour`.

Для изменения prompt нужны два результата: соответствующий prompt-уровень и
`runtime`. После деплоя отдельно нужен `contour`. Synthetic prompt-тесты — это
диагностика модели, а не доказательство текущего TEST или production.

Prompt-уровни тратят токены и требуют одновременно `--execute` и
`--confirm-model`. Если ключа нет в окружении, CLI читает только
`OPENROUTER_API_KEY` из `--env-file` (по умолчанию `.env`) и никогда его не
печатает. `contour` требует отдельный `--confirm-live`, вызывает существующий
`scripts/nmbot_v6_jivo_smoke.py` и должен запускаться в TEST-окружении, где есть
`/home/neiro/novostroy-bot/.env`.

## Проверенный пример 2026-08-17

Фактический прогон новой системы:

- Prompt1: `3/3 passed`, eval `eval-v0S-2026-08-17T10:08:13`;
- Prompt2: `5/5 passed`, eval `eval-NKE-2026-08-17T10:12:07`;
- runtime: `135 passed`;
- тесты самого runner: `9 passed`.

В Prompt2 справочный вопрос без намерения подбора вернул пустой `final_question`.
При явном подборе, где единственной недостающей деталью была площадь, модель
спросила: `Какая площадь студии вас интересует?`. Когда одновременно неизвестны
бюджет и площадь, модель воздержалась от угадывания. Это один диагностический
прогон с assertions, а не доказательство стабильности и не live TEST.
