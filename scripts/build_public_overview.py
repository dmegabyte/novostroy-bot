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
  <nav><a href="/index.html">← Все сервисы</a><a href="#history">История</a><a href="map.html">Блок‑схема на весь экран</a>{nav}</nav>
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


def main() -> None:
    OUT.mkdir(exist_ok=True)
    nmbot_dir = OUT / NMBOT_SLUG
    nmbot_dir.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(build_root_index(), encoding="utf-8")
    (nmbot_dir / "index.html").write_text(build_nmbot_index(), encoding="utf-8")
    (nmbot_dir / "map.html").write_text(read("docs/BOT_SCENARIO_MAP.html"), encoding="utf-8")
    print(f"built {OUT / 'index.html'}")
    print(f"built {nmbot_dir / 'index.html'}")
    print(f"built {nmbot_dir / 'map.html'}")


if __name__ == "__main__":
    main()
