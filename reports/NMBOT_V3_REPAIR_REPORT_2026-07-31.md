# NMBot V3 — отчёт по ремонту 2026-07-31

## Итог

Проблемный V3-подбор восстановлен: поиск возвращает варианты, клиент получает
терминальный ответ, а нестабильный V3 writer отключён. Живая подача остаётся у
manager-rewriter, который публикует ответ после детерминированного renderer.

## Подтверждённые причины

1. **Основной поиск исчерпывал fallback.** Primary возвращал валидный, но пустой
   результат; один fallback возвращал усечённый JSON, другой не успевал до
   таймаута. Это подтверждено закрытой forensic-проверкой одного воспроизведения.
2. **Первый полный release был неполным.** В immutable artifact отсутствовал
   обязательный V1 candidate prompt, поэтому API не стартовал. Atomic rollback
   сохранил предыдущий активный release.
3. **V3 writer регулярно не проходил safety-gate.** В нескольких smoke writer
   добавлял неподтверждённые измеримые факты и/или рискованные утверждения.
   Валидатор корректно не публиковал такой текст; manager-rewriter публиковал
   безопасный клиентский ответ.

## Что изменено

| Изменение | Зачем |
|---|---|
| Компактный контракт `main_search`: один MCP-вызов, ограниченный объём результата, terminal strict JSON | Снизить риск усечения structured search output. |
| Fallback main_search: `openai/gpt-5.5` вместо DeepSeek во fallback-цепочке | Убрать подтверждённый медленный/непригодный fallback из этого слоя. |
| Allowlist обязательного V1 candidate prompt + extracted `create_app()` preflight | Поймать неполный artifact до VPS cutover. |
| Закрытый opt-in forensic log с правами 0700/0600 | Один раз установить точную причину malformed search output без утечки в обычные логи. После диагностики выключен и очищен. |
| Safe semantic diagnostics V3 | Журнал показывает только класс rejection, без текста, цифр, payload, prompt или task ID. |
| V3 writer prompt grounding | Числа и измеримые факты разрешены только из canonical evidence; запрещены расчёты и рекламные обещания. |
| `NMBOT_V3_RESPONSE_COMPOSER_MODE=off` | Убраны повторяющиеся непубликуемые вызовы writer; deterministic renderer и manager-rewriter сохранены. |
| Строгая enum-валидация composer/manager mode в approved env helper | Исключить некорректные значения конфигурации до записи `.env`. |

## Выпущенные релизы

| Release | Результат |
|---|---|
| `nmbot-full-runtime-gpt55-20260731-1` | Не запустился из-за отсутствующего prompt; автоматически откатан. |
| `nmbot-full-runtime-gpt55-20260731-2` | API startup исправлен; main_search smoke вернул варианты. |
| `nmbot-full-runtime-v3diag-20260731-3` | Добавлены безопасные semantic diagnostics. |
| `nmbot-full-runtime-v3writer-20260731-1` | Усилен writer prompt; safety fallback сохранился. |
| `nmbot-full-runtime-v3numeric-20260731-1` | Диагностика классов числовых нарушений. |
| Helper overlay + config switch | V3 writer выключен штатным helper, manager-rewriter оставлен в `publish`. |

## Финальная проверка

Для V3-сценария «Однушка или студия возле парка и метро» после переключения
writer в `off` подтверждены:

- API и n8n bridge active/healthy;
- main search завершён без runtime quality blockers;
- writer и formatter skipped;
- manager-rewriter completed и published;
- клиент получил terminal `BOT_MESSAGE` примерно за 46 секунд.

Это подтверждает рабочий путь: `search → canonical cards → deterministic
renderer → manager-rewriter → BOT_MESSAGE`.

## Ограничения и дальнейший контроль

- Этот отчёт не является общим release verdict для всех V3-сценариев: проверен
  один ранее проблемный smoke по first-failure protocol.
- Точность поисковых фактов и delivery для остальных диалогов требуют отдельных
  correlated traces, facts/near/effective constraints и bridge evidence.
- Если V3 writer снова понадобится, включать сначала только в `shadow`, собрать
  privacy-safe rejection rates по сценариям и публиковать лишь после отдельного
  подтверждённого smoke.

## Источники

- `docs/NMBOT_RUNTIME_VERSIONS.md:95-133`
- `docs/NMBOT_V2_ANSWER_QUALITY_GATE.md:8-18,29-50`
- `docs/BOT_ARCHITECTURE.md:151-168`
- `docs/NMBOT_RUNBOOK.md:59-79`
- live VPS release, health и correlated Jivo smoke 2026-07-31
