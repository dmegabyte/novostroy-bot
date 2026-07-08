#!/usr/bin/env python3
"""Build static public pages for the shared VPS web service.

Output is intentionally static and allow-listed. It is meant to be copied to
`/home/neiro/public`, which is already served by `python3 -m http.server 8765`.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "public_site"
NMBOT_SLUG = "nmbot-project-7f3a9c"


@dataclass(frozen=True)
class SourceDoc:
    key: str
    title: str
    path: str
    group: str
    note: str


DOCS: tuple[SourceDoc, ...] = (
    SourceDoc("readme", "README — общий контекст", "README.md", "ТЗ проекта", "Что это за бот, где прод и как устроен dev/prod."),
    SourceDoc("product", "PRODUCT_TZ — продуктовое ТЗ", "docs/PRODUCT_TZ.md", "ТЗ проекта", "Продуктовые требования и ограничения."),
    SourceDoc("architecture", "BOT_ARCHITECTURE — архитектура", "docs/BOT_ARCHITECTURE.md", "ТЗ проекта", "Компоненты, state, модели, MCP, deploy."),
    SourceDoc("decision_architecture", "LLM_DECISION_ARCHITECTURE_TZ — новая архитектура решений", "docs/LLM_DECISION_ARCHITECTURE_TZ.md", "ТЗ проекта", "Decision Context Builder, Action Resolver, Presenter, Validator."),
    SourceDoc("ideal", "IDEAL_IRINA_UX — эталон UX", "docs/IDEAL_IRINA_UX.md", "ТЗ проекта", "Как Ирина должна говорить с клиентом."),
    SourceDoc("dialogue", "IRINA_DIALOGUE_MAP_V1 — сценарии", "docs/IRINA_DIALOGUE_MAP_V1.md", "Сценарии", "Stage 0/1/2/3/4/4.5/5/6 и переходы."),
    SourceDoc("enrichment", "SCENARIO_COMMENT_ENRICHMENT_TZ — MCP факты", "docs/SCENARIO_COMMENT_ENRICHMENT_TZ.md", "Сценарии", "Какие MCP-поля используются для семьи, инвестиций, переезда."),
    SourceDoc("checklist", "IRINA_UX_RELEASE_CHECKLIST", "docs/IRINA_UX_RELEASE_CHECKLIST.md", "Контроль качества", "Что проверять перед выпуском."),
    SourceDoc("experiments", "EXPERIMENTS — гипотезы", "docs/EXPERIMENTS.md", "Контроль качества", "Журнал гипотез и release gate."),
    SourceDoc("changelog", "CHANGELOG", "docs/CHANGELOG.md", "Контроль качества", "История изменений."),
    SourceDoc("search_prompt", "prompts/search_v1.txt — активный MCP/search промт", "prompts/search_v1.txt", "Активные промты", "Что просим у MCP novostroym и какие поля ждём."),
    SourceDoc("chat_prompt", "prompts/chat_v1.txt — активный chat промт", "prompts/chat_v1.txt", "Активные промты", "Как Ирина собирает клиентский ответ."),
    SourceDoc("style_prompt", "prompts/text_style_v1.txt — стиль", "prompts/text_style_v1.txt", "Активные промты", "Правила живого русского текста."),
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def truncate(text: str, limit: int = 70_000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n…[обрезано для веб-страницы: всего {len(text)} символов]"


def shell(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --bg:#f6f7fb; --card:#fff; --ink:#172033; --muted:#667085; --line:#d8dee9; --blue:#2563eb; --dark:#101828; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Inter,system-ui,-apple-system,Segoe UI,sans-serif; background:var(--bg); color:var(--ink); line-height:1.55; }}
    header {{ padding:40px 28px 24px; background:linear-gradient(135deg,#101828,#1e3a8a 58%,#0e7490); color:white; }}
    header h1 {{ margin:0 0 10px; font-size:clamp(30px,4vw,52px); line-height:1.05; }}
    header p {{ margin:0; max-width:1040px; color:#dbeafe; font-size:18px; }}
    main {{ max-width:1280px; margin:0 auto; padding:28px; }}
    nav {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:24px; }}
    nav a, .button {{ display:inline-block; padding:10px 13px; border-radius:999px; background:#e0ecff; color:#174ea6; text-decoration:none; font-weight:800; }}
    section {{ margin:24px 0; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:20px; box-shadow:0 8px 24px rgba(15,23,42,.08); padding:22px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:16px; }}
    .flow {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
    .node {{ background:white; border:1px solid var(--line); border-radius:14px; padding:12px 14px; font-weight:900; }}
    .arrow {{ color:#64748b; font-size:22px; }}
    details {{ background:white; border:1px solid var(--line); border-radius:16px; margin:14px 0; overflow:hidden; }}
    summary {{ cursor:pointer; padding:14px 16px; font-weight:900; background:#f8fafc; }}
    pre {{ margin:0; padding:16px; white-space:pre-wrap; word-break:break-word; background:#0b1220; color:#dbeafe; overflow:auto; font-size:13px; line-height:1.45; }}
    .muted {{ color:var(--muted); }}
    .tag {{ display:inline-block; margin:3px; padding:4px 9px; border-radius:999px; background:#ecfeff; color:#0e7490; font-size:12px; font-weight:900; }}
    iframe {{ width:100%; min-height:78vh; border:1px solid var(--line); border-radius:18px; background:white; }}
  </style>
</head>
<body>
  {body}
</body>
</html>"""


def build_root_index() -> str:
    body = f"""
<header>
  <h1>Внутренние веб‑сервисы</h1>
  <p>Один публичный вход на текущие панели. Выберите нужный сервис.</p>
</header>
<main>
  <section class="grid">
    <article class="card">
      <h2>MPN quality dashboard</h2>
      <p>Интерактивный пульт контроля качества разметки: mismatch rate, теги, звонки и проблемные строки.</p>
      <p><a class="button" href="/mpn-quality-7f3a9c/index.html">Открыть MPN quality</a></p>
    </article>
    <article class="card">
      <h2>NMBOT / Ирина — проект целиком</h2>
      <p>ТЗ проекта, активные промты, MCP-поля, блок‑схема сценариев, state и реальные примеры ответов.</p>
      <p><a class="button" href="/{NMBOT_SLUG}/index.html">Открыть NMBOT overview</a></p>
    </article>
  </section>
</main>
"""
    return shell("Внутренние веб-сервисы", body)


def grouped_docs() -> dict[str, list[SourceDoc]]:
    groups: dict[str, list[SourceDoc]] = {}
    for doc in DOCS:
        groups.setdefault(doc.group, []).append(doc)
    return groups


def build_nmbot_index() -> str:
    nav = "".join(f"<a href='#{html.escape(group)}'>{html.escape(group)}</a>" for group in grouped_docs())
    blocks: list[str] = []
    for group, docs in grouped_docs().items():
        inner = []
        for doc in docs:
            text = truncate(read(doc.path))
            inner.append(
                f"<details id='{html.escape(doc.key)}'><summary>{html.escape(doc.title)}"
                f"<span class='muted'> — {html.escape(doc.note)}</span></summary>"
                f"<pre>{html.escape(text)}</pre></details>"
            )
        blocks.append(f"<section id='{html.escape(group)}' class='card'><h2>{html.escape(group)}</h2>{''.join(inner)}</section>")

    mcp_example = {
        "facts": [
            {
                "name": "ЖК Лучи",
                "location": "Солнцево",
                "price_range": "от 10,89 млн ₽",
                "ready": "дом сдан",
                "finishing": "есть квартиры с отделкой",
                "schools": "2 школы",
                "kindergartens": "4 детских сада",
                "parks": "Мещерский парк; Чоботовский лес",
                "clinics": "аптеки",
            }
        ],
        "near": [],
        "missing": [],
        "params": {"purpose": "family", "rooms": "2", "max_price": "15000000"},
    }
    body = f"""
<header>
  <h1>NMBOT / Ирина — проект целиком</h1>
  <p>Живая карта проекта: что делает бот, какие сценарии поддерживает, какие промты активны и какие MCP-поля реально используются.</p>
</header>
<main>
  <nav><a href="/index.html">← Все сервисы</a><a href="architecture-v2.html">Архитектура для новичка</a><a href="#history">История</a><a href="map.html">Блок‑схема на весь экран</a>{nav}</nav>
  <section class="card">
    <h2>Общая логика</h2>
    <p><strong>Жёсткое правило:</strong> любой запрос о квартире, новостройке, ЖК или подборе вариантов сначала проходит через инструментальный MCP/search. Ирина не имеет права отвечать по памяти модели: цены, площади, сроки, отделка, инфраструктура и варианты берутся только из результата поиска.</p>
    <div class="flow">
      <div class="node">Telegram</div><div class="arrow">→</div>
      <div class="node">State / visible_options</div><div class="arrow">→</div>
      <div class="node">LLM planner</div><div class="arrow">→</div>
      <div class="node">MCP/search</div><div class="arrow">→</div>
      <div class="node">Normalizer</div><div class="arrow">→</div>
      <div class="node">Presenter</div><div class="arrow">→</div>
      <div class="node">Ответ Ирины</div>
    </div>
    <p><span class="tag">first_list</span><span class="tag">selected_object</span><span class="tag">recommend_options</span><span class="tag">operator_live_check</span><span class="tag">phone_capture</span></p>
  </section>
  <section class="card">
    <h2>Блок‑схема сценариев</h2>
    <p>Это та самая большая схема с блоками, стрелками, промтами, MCP-полями и примерами.</p>
    <iframe src="map.html"></iframe>
  </section>
  <section class="card">
    <h2>Пример MCP/search ответа</h2>
    <p>Даже если клиент написал неполный запрос, поиск всё равно запускается по доступным параметрам, а недостающие условия попадают в <code>missing</code> / <code>params</code>.</p>
    <pre>{html.escape(json.dumps(mcp_example, ensure_ascii=False, indent=2))}</pre>
  </section>
  <section id="history" class="card">
    <h2>История последних диалогов</h2>
    <p class="muted">Обновляется автоматически каждые 10 секунд из <code>history.json</code>. Телефоны, email и токены маскируются, длинный MCP/search trace обрезается.</p>
    <p><button class="button" type="button" onclick="loadHistory()">Обновить сейчас</button> <span id="history-status" class="muted">загрузка…</span></p>
    <div id="history-list"></div>
  </section>
  {''.join(blocks)}
</main>
<script>
const historyList = document.getElementById('history-list');
const historyStatus = document.getElementById('history-status');
function esc(value) {{
  return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}
function pretty(value) {{
  if (!value) return '';
  if (typeof value === 'string') return value;
  try {{ return JSON.stringify(value, null, 2); }} catch (e) {{ return String(value); }}
}}
function renderHistory(payload) {{
  const items = payload.items || [];
  historyStatus.textContent = `обновлено: ${{payload.generated_at || '—'}} · записей: ${{items.length}}`;
  if (!items.length) {{
    historyList.innerHTML = '<p class="muted">Истории пока нет: нет свежих user_message в публичном history.json.</p>';
    return;
  }}
  historyList.innerHTML = items.map((item, idx) => `
    <details open>
      <summary>#${{idx + 1}} · ${{esc(item.ts)}} · turn ${{esc(item.turn_id)}} · ${{esc(item.dialog_id)}}</summary>
      <div style="padding:16px">
        <p><strong>Клиент:</strong><br>${{esc(item.user)}}</p>
        <p><strong>Ирина:</strong><br>${{esc(item.bot)}}</p>
        <p><strong>Intent:</strong></p><pre>${{esc(pretty(item.intent))}}</pre>
        ${{item.plan ? `<p><strong>Plan:</strong></p><pre>${{esc(pretty(item.plan))}}</pre>` : ''}}
        ${{item.mcp_search ? `<p><strong>MCP/search:</strong></p><pre>${{esc(pretty(item.mcp_search))}}</pre>` : ''}}
        ${{item.buttons ? `<p><strong>Buttons:</strong></p><pre>${{esc(pretty(item.buttons))}}</pre>` : ''}}
        ${{item.cost ? `<p><strong>Cost:</strong></p><pre>${{esc(pretty(item.cost))}}</pre>` : ''}}
      </div>
    </details>
  `).join('');
}}
async function loadHistory() {{
  try {{
    const response = await fetch('history.json?ts=' + Date.now(), {{cache:'no-store'}});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    renderHistory(await response.json());
  }} catch (error) {{
    historyStatus.textContent = 'не удалось загрузить history.json: ' + error.message;
  }}
}}
loadHistory();
setInterval(loadHistory, 10000);
</script>
"""
    return shell("NMBOT / Ирина — проект целиком", body)


def build_architecture_v2_page() -> str:
    text = truncate(read("docs/LLM_DECISION_ARCHITECTURE_TZ.md"), limit=120_000)
    summary_cards = "".join(
        (
            "<article class='card'><h3>Для чего эта схема</h3>"
            "<p>Показать, как Ирина принимает решение без сырого хаоса в LLM: сначала смысл запроса, потом проверка данных, потом безопасный ответ.</p></article>"
        ),
        )
    summary_cards += "".join(
        [
            "<article class='card'><h3>Что здесь главное</h3>"
            "<p>LLM не должна сама разбирать весь MCP и помнить все запреты. Для этого есть Decision Context Builder, Action Resolver и Safety Validator.</p></article>",
            "<article class='card'><h3>Что увидит человек</h3>"
            "<p>Короткую схему блоков, понятные сценарии и раскрывающиеся пояснения. В конце — полный текст ТЗ, если нужен совсем глубокий разбор.</p></article>",
        ]
    )
    block_cards = [
        ("1. State / Memory", "Это память диалога. Здесь лежат параметры, выбранный ЖК, предыдущие варианты и флаги вроде <code>awaiting_phone</code>.", "State не показывается клиенту и не должен напрямую превращаться в ответ."),
        ("2. Intent Planner LLM", "Этот слой понимает, что клиент сейчас хочет: новый поиск, широкий подбор, совет, сравнение, оператора или что-то непонятное.", "Planner не пишет ответ, а выбирает семантическое действие."),
        ("3. Search Decision", "Здесь решается, нужен ли новый MCP/search. Для нового квартирного запроса поиск обязателен, для вопроса про критерии сравнения — нет.", "Это экономит лишние поиски и не зацикливает диалог."),
        ("4. MCP/search novostroym", "Источник фактов. Только отсюда можно брать цену, площадь, отделку, срок, инфраструктуру и сам список ЖК.", "Если ЖК нет в structured facts/near, его нельзя показывать клиенту как факт."),
        ("5. Normalizer", "Превращает сырой ответ поиска в безопасные карточки: exact / near_only, client_facts, why_close, missing, do_not_say.", "Так LLM не видит сырой JSON и не путает близкий вариант с точным."),
        ("6. Decision Context Builder", "Сжимает всё в короткую карточку: что хочет клиент, что найдено, какие риски есть и что вообще разрешено делать.", "Это главный слой, который облегчает работу LLM."),
        ("7. Action Resolver", "Проверяет, можно ли выполнить выбранное действие: показать точные варианты, объяснить различие near, предложить оператора или задать один вопрос.", "Код не придумывает смысл, а только страхует опасные переходы."),
        ("8. Presenter", "Пишет человеческий ответ: короткий первый список, объяснение критериев сравнения, совет, операторская передача или мягкий отказ.", "Здесь важен живой язык, но без выдуманных фактов."),
        ("9. Safety Validator", "Финальная проверка: нет ли сырого dict, лишних ЖК, раннего оператора, технических слов и неподтверждённой финтематики.", "Если проверка не прошла, ответ не выпускается как есть."),
    ]
    block_details = "".join(
        f"<details><summary>{html.escape(title)}</summary><p>{html.escape(short)}</p><p class='muted'>{html.escape(extra)}</p></details>"
        for title, short, extra in block_cards
    )
    scenario_details = "".join(
        [
            "<details><summary>Первый подбор: клиент просто хочет варианты</summary><p>Planner видит <code>new_search</code> или <code>wide_search</code>. Если факты есть — показываем 2–3 варианта. Если бюджет не назван, не мучаем его кругом одинаковых вопросов, а даём широкий стартовый список.</p></details>",
            "<details><summary>near-only: точных вариантов нет, есть близкие</summary><p>Система честно говорит, что это не exact match. В ответе обязательно есть отличие: почему вариант только близкий и что именно не совпало.</p></details>",
            "<details><summary>По каким критериям сравнивали?</summary><p>Это не повтор списка, а объяснение логики сравнения: цена, отделка, срок, локация, метро, инфраструктура — только если эти поля реально есть в данных.</p></details>",
            "<details><summary>Что посоветуешь?</summary><p>Это отдельный совет из уже видимых вариантов. Система выбирает один лучший по сценарию и объясняет, почему он выглядит сильнее остальных.</p></details>",
            "<details><summary>Как связаться с оператором?</summary><p>Если клиент просит человека, а не ещё один список, бот сохраняет контекст и просит номер для связи. Вопрос не должен превращаться в новый выбор между первым, вторым и третьим.</p></details>",
            "<details><summary>Без ПВ / траншевая ипотека</summary><p>Это отдельный сценарий: если у данных нет подтверждения по программам, бот не выдумывает банковскую схему и честно говорит, что надо проверить у оператора или в подтверждённом MCP-источнике.</p></details>",
            "<details><summary>Нестандартный вопрос</summary><p>Если вопрос связан с недвижимостью, но сценарий непривычный, бот не фантазирует. Он либо отвечает только по подтверждённому факту, либо честно признаёт, что факта нет, и возвращает к проекту.</p></details>",
        ]
    )
    example_cards = [
        (
            "Как выглядит безопасная карточка",
            {
                "stage": "comparison_followup",
                "search_summary": {"facts_count": 0, "near_count": 1, "has_exact": False, "has_near": True},
                "risk_flags": ["near_only", "must_explain_difference"],
                "allowed_actions": ["explain_comparison_criteria", "show_near_with_difference"],
                "recommended_action": "explain_comparison_criteria",
            },
        ),
        (
            "Как выглядит обычный первый поиск",
            {
                "stage": "first_list",
                "search_summary": {"facts_count": 3, "near_count": 0, "has_exact": True, "has_near": False},
                "allowed_actions": ["show_exact_options", "ask_one_clarification"],
                "recommended_action": "show_exact_options",
            },
        ),
    ]
    example_details = "".join(
        f"<details><summary>{html.escape(title)}</summary><pre>{html.escape(json.dumps(payload, ensure_ascii=False, indent=2))}</pre></details>"
        for title, payload in example_cards
    )
    body = f"""
<header>
  <h1>Новая архитектура Ирины</h1>
  <p>Понятное ТЗ для человека, который не в проекте: сначала короткое объяснение, потом схема, потом раскрывающиеся блоки и примеры.</p>
</header>
<main>
  <nav><a href="/index.html">← Все сервисы</a><a href="index.html">NMBOT overview</a><a href="map.html">Блок‑схема</a><a href="index.html#history">История</a></nav>
  <section class="card">
    <h2>Коротко по-человечески</h2>
    <p>Главная идея: Ирина не должна сама разбирать сырой MCP/search и помнить все запреты в голове. Между данными и ответом стоит отдельный слой <strong>Decision Context Builder</strong>, который готовит короткую безопасную карточку ситуации: что хочет клиент, что найдено, что можно говорить и что делать дальше.</p>
    <div class="grid">{summary_cards}</div>
  </section>
  <section class="card">
    <h2>Как читать схему</h2>
    <div class="flow">
      <div class="node">User message</div><div class="arrow">→</div>
      <div class="node">Planner LLM</div><div class="arrow">→</div>
      <div class="node">MCP/search</div><div class="arrow">→</div>
      <div class="node">Normalizer</div><div class="arrow">→</div>
      <div class="node">Decision Context</div><div class="arrow">→</div>
      <div class="node">Action Resolver</div><div class="arrow">→</div>
      <div class="node">Presenter</div><div class="arrow">→</div>
      <div class="node">Validator</div>
    </div>
    <p class="muted">Первые блоки думают и готовят данные. Последние блоки решают, можно ли это показывать клиенту и как сказать это по-человечески.</p>
  </section>
  <section class="card">
    <h2>Что делает каждый блок</h2>
    {block_details}
  </section>
  <section class="card">
    <h2>Типовые сценарии</h2>
    {scenario_details}
  </section>
  <section class="card">
    <h2>Примеры безопасной карточки</h2>
    {example_details}
  </section>
  <section class="card">
    <h2>Полный текст ТЗ</h2>
    <details>
      <summary>Открыть полный технический документ</summary>
      <pre>{html.escape(text)}</pre>
    </details>
  </section>
</main>
"""
    return shell("NMBOT — новая архитектура решений", body)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    nmbot_dir = OUT / NMBOT_SLUG
    nmbot_dir.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(build_root_index(), encoding="utf-8")
    (nmbot_dir / "index.html").write_text(build_nmbot_index(), encoding="utf-8")
    (nmbot_dir / "architecture-v2.html").write_text(build_architecture_v2_page(), encoding="utf-8")
    (nmbot_dir / "map.html").write_text(read("docs/BOT_SCENARIO_MAP.html"), encoding="utf-8")
    print(f"built {OUT / 'index.html'}")
    print(f"built {nmbot_dir / 'index.html'}")
    print(f"built {nmbot_dir / 'architecture-v2.html'}")
    print(f"built {nmbot_dir / 'map.html'}")


if __name__ == "__main__":
    main()
