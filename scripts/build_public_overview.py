#!/usr/bin/env python3
"""Build safe static public pages for the shared VPS web service.

The generator intentionally publishes summaries only. It must never export raw
docs, prompts, traces, logs, backups, dotenv content, operational snippets or
secret key names.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).parent.parent.absolute()
OUT = ROOT / "public_site"
NMBOT_SLUG = "nmbot-project-7f3a9c"
NMBOT_URL = f"http://193.107.155.236:8765/{NMBOT_SLUG}/"
UPDATED_AT = "2026-07-23"


@dataclass(frozen=True)
class SourceDoc:
    key: str
    title: str
    path: str
    group: str
    note: str


DOCS: tuple[SourceDoc, ...] = (
    SourceDoc("runtime_versions", "Runtime versions", "docs/NMBOT_RUNTIME_VERSIONS.md", "Архитектура", "V0, V2 и V3: разные владельцы диалога после общего selector."),
    SourceDoc("architecture", "Bot architecture", "docs/BOT_ARCHITECTURE.md", "Архитектура", "Jivo transport, selector, state, search и safety boundaries."),
    SourceDoc("runtime_registry", "Runtime registry", "docs/NMBOT_RUNTIME_REGISTRY.md", "Архитектура", "Safe summary of supported selector/composer modes without operational values."),
    SourceDoc("ideal", "Irina UX north star", "docs/IDEAL_IRINA_UX.md", "Сценарии и UX", "Как отвечать живо и полезно без выдуманных фактов."),
    SourceDoc("dialogue", "Dialogue map", "docs/IRINA_DIALOGUE_MAP_V1.md", "Сценарии и UX", "Стадии и переходы диалога в безопасном пересказе."),
    SourceDoc("diagnostics", "Diagnostics overview", "docs/JIVO_DIAGNOSTICS.md", "Контроль", "Какие классы проверок существуют; без trace, логов и команд."),
    SourceDoc("prompt_provenance", "Prompt provenance", "docs/NMBOT_PROMPT_PROVENANCE.md", "Контроль", "Идентичность prompt set без публикации prompt bodies."),
    SourceDoc("release", "Release identity", "docs/NMBOT_RELEASE_IDENTITY.md", "Контроль", "Как связываются исходники и диалог; без deploy-инструкций."),
    SourceDoc("changelog", "Changelog", "docs/CHANGELOG.md", "Контроль", "История изменений в кратком публичном виде."),
)


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="replace")


def page_url(filename: str = "index.html") -> str:
    if filename == "index.html":
        return NMBOT_URL
    return f"{NMBOT_URL}{filename}"


def shell(title: str, body: str, *, description: str, canonical: str) -> str:
    favicon = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'%3E%3Crect width='64' height='64' rx='14' fill='%230b1730'/%3E%3Cpath d='M18 42V20h7l14 22V20h7v22h-7L25 20v22z' fill='%23e8f7f7'/%3E%3C/svg%3E"
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="description" content="{html.escape(description)}" />
  <link rel="canonical" href="{html.escape(canonical)}" />
  <meta name="robots" content="index,follow" />
  <meta property="og:title" content="{html.escape(title)}" />
  <meta property="og:description" content="{html.escape(description)}" />
  <meta property="og:type" content="website" />
  <meta property="og:url" content="{html.escape(canonical)}" />
  <link rel="icon" href="{favicon}" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{ --canvas:#f4f7fb; --surface:rgba(255,255,255,.9); --ink:#10213d; --muted:#5d6b82; --line:#dbe4ef; --blue:#2563eb; --teal:#0f8b8d; --navy:#0b1730; --shadow:0 18px 48px rgba(28,54,90,.10); }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; overflow-x:hidden; font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; background:radial-gradient(circle at 85% -5%,#d7f6f6 0,transparent 28rem),radial-gradient(circle at 5% 18%,#e1ebff 0,transparent 26rem),var(--canvas); color:var(--ink); font-size:17px; line-height:1.6; }}
    .skip-link {{ position:absolute; left:16px; top:10px; z-index:20; transform:translateY(-160%); padding:10px 14px; border-radius:12px; background:#fff; color:var(--navy); font-weight:800; }}
    .skip-link:focus-visible {{ transform:translateY(0); }}
    header {{ position:relative; overflow:hidden; padding:clamp(34px,7vw,78px) max(20px,calc((100vw - 1120px)/2)) clamp(36px,5vw,52px); background:linear-gradient(120deg,#08152d,#102c5d 58%,#0d777e); color:white; }}
    header::after {{ content:""; position:absolute; width:28rem; height:28rem; border-radius:50%; right:-13rem; top:-18rem; border:1px solid rgba(255,255,255,.18); box-shadow:0 0 0 4rem rgba(255,255,255,.035),0 0 0 8rem rgba(255,255,255,.025); pointer-events:none; }}
    header > * {{ position:relative; z-index:1; }}
    header h1 {{ margin:0 0 13px; max-width:860px; font-size:clamp(2.1rem,5vw,4.4rem); letter-spacing:-.052em; line-height:1; }}
    header p {{ margin:0; max-width:760px; color:#d6e7ff; font-size:clamp(1rem,1.8vw,1.18rem); }}
    main {{ max-width:1120px; margin:0 auto; padding:22px 20px 60px; }}
    nav {{ position:sticky; z-index:5; top:10px; display:flex; flex-wrap:wrap; gap:6px; width:100%; max-width:100%; margin:-28px 0 28px; padding:8px; border:1px solid rgba(219,228,239,.82); border-radius:18px; background:rgba(255,255,255,.82); backdrop-filter:blur(18px) saturate(160%); box-shadow:0 12px 30px rgba(20,42,75,.09); }}
    nav a, .button {{ display:inline-flex; align-items:center; justify-content:center; min-height:44px; padding:10px 14px; border-radius:12px; color:#244269; text-decoration:none; font-size:.92rem; font-weight:780; transition:transform 150ms cubic-bezier(.23,1,.32,1),background-color 150ms ease,color 150ms ease,box-shadow 150ms ease; }}
    .button {{ border:0; background:#2563eb; color:#fff; box-shadow:0 8px 18px rgba(37,99,235,.22); cursor:pointer; }}
    nav a:active, .button:active {{ transform:scale(.98); }}
    a:focus-visible, button:focus-visible, summary:focus-visible {{ outline:3px solid #fbbf24; outline-offset:3px; }}
    section {{ margin:20px 0; }}
    .card {{ background:var(--surface); border:1px solid rgba(219,228,239,.95); border-radius:24px; box-shadow:var(--shadow); padding:clamp(19px,3vw,32px); }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(245px,1fr)); gap:14px; }}
    .hero-grid {{ display:grid; grid-template-columns:1.1fr .9fr; gap:18px; align-items:stretch; }}
    .eyebrow {{ display:block; margin-bottom:10px; color:var(--teal); font-size:.76rem; font-weight:860; letter-spacing:.1em; text-transform:uppercase; }}
    h2 {{ margin:0 0 10px; font-size:clamp(1.35rem,2.6vw,1.9rem); letter-spacing:-.03em; line-height:1.15; }}
    h3 {{ margin:0 0 8px; font-size:1.08rem; letter-spacing:-.02em; line-height:1.25; }}
    p {{ margin:0 0 12px; }}
    .muted {{ color:var(--muted); }}
    .principle {{ border-radius:20px; padding:22px; color:#dcecff; background:linear-gradient(145deg,#10274e,#0c5f69); }}
    .principle strong {{ color:#fff; }}
    .tag {{ display:inline-flex; margin:3px 3px 0 0; padding:5px 9px; border-radius:999px; background:#e8f7f7; color:#087477; font-size:.75rem; font-weight:820; }}
    .version {{ position:relative; overflow:hidden; min-height:218px; padding:23px; border:1px solid var(--line); border-radius:20px; background:#fff; }}
    .version::before {{ content:""; position:absolute; inset:0 auto 0 0; width:5px; background:var(--blue); }}
    .version.v0::before {{ background:#7c3aed; }} .version.v2::before {{ background:#0f8b8d; }} .version.v3::before {{ background:#2563eb; }}
    .version p {{ color:var(--muted); }} .version code, .node code {{ color:#1a4d89; font-weight:780; }}
    .branch-flow {{ display:grid; grid-template-columns:1fr; gap:10px; margin-top:18px; }}
    .flow-row {{ display:grid; grid-template-columns:repeat(5,minmax(120px,1fr)); gap:8px; align-items:stretch; }}
    .flow-row.shared {{ grid-template-columns:repeat(2,minmax(180px,1fr)); max-width:560px; }}
    .node {{ min-height:82px; display:flex; align-items:center; justify-content:center; padding:12px; border:1px solid #d9e5f5; border-radius:16px; background:linear-gradient(145deg,#fff,#f3f8ff); color:#1f416e; font-size:.88rem; font-weight:820; line-height:1.22; text-align:center; }}
    .node.accent {{ border-color:#95e4df; background:linear-gradient(145deg,#effcfa,#dcf6f4); color:#096466; }}
    .node.v0 {{ border-color:#ddd0ff; background:linear-gradient(145deg,#fff,#f6f0ff); color:#5b21b6; }}
    .flow-label {{ margin:8px 0 2px; color:#49617f; font-weight:850; }}
    .schema-list {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; padding:0; list-style:none; }}
    .schema-list li {{ padding:12px; border:1px solid var(--line); border-radius:15px; background:#fff; }}
    details {{ border:1px solid var(--line); border-radius:16px; margin:12px 0; overflow:hidden; background:#fff; }}
    summary {{ cursor:pointer; min-height:44px; padding:15px 16px; font-weight:800; background:#fbfdff; }}
    details[open] summary {{ border-bottom:1px solid var(--line); }}
    pre {{ margin:0; padding:18px; white-space:pre-wrap; word-break:break-word; background:#0c1830; color:#d8e8ff; overflow:auto; font-size:.82rem; line-height:1.55; }}
    footer {{ max-width:1120px; margin:0 auto; padding:0 20px 42px; color:#5d6b82; }}
    footer .footer-inner {{ border-top:1px solid var(--line); padding-top:18px; display:flex; flex-wrap:wrap; gap:12px 18px; justify-content:space-between; overflow-wrap:anywhere; }}
    footer a {{ color:#244269; font-weight:760; }}
    @media (hover:hover) and (pointer:fine) {{ nav a:hover, .button:hover {{ background-color:#e7f0ff; color:#164bb5; }} .button:hover {{ background-color:#1d4ed8; color:#fff; }} }}
    @media (max-width:820px) {{ body {{ font-size:16px; }} header {{ padding-top:32px; }} .hero-grid {{ grid-template-columns:1fr; }} .flow-row, .flow-row.shared {{ grid-template-columns:1fr; max-width:none; }} .node {{ min-height:58px; justify-content:flex-start; text-align:left; }} nav {{ position:relative; top:auto; margin:-18px 0 22px; overflow-x:auto; flex-wrap:nowrap; }} nav a {{ white-space:nowrap; }} }}
    @media (max-width:430px) {{ main {{ padding-inline:16px; }} header {{ padding-inline:18px; }} header h1 {{ font-size:2rem; }} .card,.version,.principle {{ border-radius:18px; }} }}
    @media (prefers-reduced-motion:reduce) {{ html {{ scroll-behavior:auto; }} *,*::before,*::after {{ transition-duration:0.01ms !important; animation-duration:0.01ms !important; animation-iteration-count:1 !important; }} }}
  </style>
</head>
<body>
  <a class="skip-link" href="#content">Перейти к содержанию</a>
  {body}
</body>
</html>"""


def site_footer() -> str:
    return f"""
<footer>
  <div class="footer-inner">
    <div>Публичная граница: только безопасные резюме, без внутренних документов, prompt-текстов, команд, диагностических данных, конфигураций и резервных копий. Обновлено: {UPDATED_AT}.</div>
    <div><a href="versions.html">Версии</a> · <a href="resources.html">Источники</a> · <a href="archive.html">Архив</a></div>
  </div>
</footer>
"""


def build_root_index() -> str:
    body = f"""
<header role="banner">
  <h1>Внутренние веб‑сервисы</h1>
  <p>Статические обзоры текущих сервисов. Они не доказывают live production-статус.</p>
</header>
<main id="content">
  <section class="grid" aria-label="Сервисы">
    <article class="card">
      <h2>MPN quality dashboard</h2>
      <p class="muted">Интерактивный пульт контроля качества разметки: mismatch rate, теги, звонки и проблемные строки.</p>
      <p><a class="button" href="/mpn-quality-7f3a9c/index.html">Открыть MPN quality</a></p>
    </article>
    <article class="card">
      <h2>NMBOT — Jivo runtime</h2>
      <p class="muted">Короткая публичная карта Jivo transport, selector и версий V0/V2/V3 без внутренних материалов.</p>
      <p><a class="button" href="/{NMBOT_SLUG}/index.html">Открыть NMBOT overview</a></p>
    </article>
  </section>
</main>
"""
    return shell("Внутренние веб-сервисы", body, description="Статический вход к публичным обзорам внутренних веб-сервисов.", canonical="http://193.107.155.236:8765/")


def build_nmbot_index() -> str:
    body = f"""
<header role="banner">
  <span class="eyebrow">Архитектурный обзор · Jivo</span>
  <h1>Три версии бота. Один безопасный транспорт.</h1>
  <p>Короткая публичная карта: Jivo transport и selector общие, а после selector V0, V2 и V3 идут по своим контрактам.</p>
</header>
<main id="content">
  <nav aria-label="Основная навигация"><a href="/index.html">← Все сервисы</a><a href="#rule">Правило</a><a href="#flow">Поток</a><a href="#versions">Версии</a><a href="versions.html">Паспорт</a><a href="architecture-v2.html">Архитектура решений</a><a href="resources.html">Ресурсы</a></nav>
  <section id="rule" class="hero-grid" aria-labelledby="rule-title">
    <article class="card">
      <span class="eyebrow">Главное правило</span>
      <h2 id="rule-title">Модель формулирует. Код принимает решение.</h2>
      <p class="muted">Факты о квартире и ЖК приходят из подтверждённого search-контракта. Runtime выбирает маршрут, порядок карточек, сценарий и следующий вопрос. Модель может улучшить форму ответа только внутри этих границ.</p>
    </article>
    <aside class="principle" aria-label="Граница надёжности">
      <span class="eyebrow" style="color:#9de7e7">Data / safety boundary</span>
      <p><strong>Deterministic-ответ строится всегда.</strong></p>
      <p>Для typed ветки V2/V3 composer — только слой формулировки после валидации. Ошибка, пустой результат или нарушение контракта оставляют безопасный fallback.</p>
      <div><span class="tag">confirmed facts</span><span class="tag">typed cards</span><span class="tag">safe fallback</span></div>
    </aside>
  </section>
  <section id="flow" class="card" aria-labelledby="flow-title">
    <span class="eyebrow">Точная архитектура</span>
    <h2 id="flow-title">Общий transport, потом разные ветки</h2>
    <div class="branch-flow" aria-label="Ветвящаяся архитектурная схема">
      <div class="flow-row shared"><div class="node accent">Jivo transport<br>lock · dedup · delivery</div><div class="node accent">Runtime selector<br>V0 / V2 / V3</div></div>
      <p class="flow-label">V0 · Валерия — отдельная двух-prompt ветка</p>
      <div class="flow-row"><div class="node v0"><code>scenario_search</code></div><div class="node v0">validated brief</div><div class="node v0"><code>answer</code></div><div class="node v0">deterministic V0 fallback</div><div class="node accent">BOT_MESSAGE</div></div>
      <p class="flow-label">V2/V3 — общая typed ветка после собственного semantic step</p>
      <div class="flow-row"><div class="node">V2 semantic planner<br>или V3 IntentPlanV3</div><div class="node">typed state/search</div><div class="node">canonical cards</div><div class="node">ResponsePlan + deterministic fallback</div><div class="node accent">validated composer → BOT_MESSAGE</div></div>
    </div>
  </section>
  <section id="versions" class="card" aria-labelledby="versions-title">
    <span class="eyebrow">Три версии</span>
    <h2 id="versions-title">Версия — это отдельный договор с клиентом</h2>
    <div class="grid">
      <article class="version v0"><span class="eyebrow">V0 · Валерия</span><h3>Изолированный brief → answer</h3><p><code>scenario_search</code> собирает validated brief, затем <code>answer</code> отвечает только по нему. V0 не проходит через общий V2/V3 Semantic plan.</p></article>
      <article class="version v2"><span class="eyebrow">V2 · Ирина</span><h3>Typed runtime + ResponsePlan</h3><p>Semantic planner → typed state → search/enrichment → canonical cards → scenario recipe → ResponsePlan → deterministic renderer.</p></article>
      <article class="version v3"><span class="eyebrow">V3 · Светлана</span><h3>IntentPlanV3 поверх typed runtime</h3><p>Начинает с IntentPlanV3: goal, viewpoint, constraints и requested facts. Дальше использует общие V2 typed cards и ResponsePlan.</p></article>
    </div>
  </section>
  <section class="card" aria-labelledby="data-title">
    <span class="eyebrow">Подтверждённые поля</span>
    <h2 id="data-title">JSON не показывается простынёй</h2>
    <p class="muted">Публично показываем компактную схему: какие классы полей допустимы в ответе. Большой raw payload, trace и внутренние diagnostics не публикуются.</p>
    <ul class="schema-list">
      <li><strong>facts[]</strong><br><span class="muted">название, локация, цена, срок, отделка, инфраструктура</span></li>
      <li><strong>near[]</strong><br><span class="muted">близкие варианты с честным отличием от запроса</span></li>
      <li><strong>missing[]</strong><br><span class="muted">что не подтверждено и не должно превращаться в догадку</span></li>
      <li><strong>params</strong><br><span class="muted">безопасные параметры поиска: цель, комнаты, бюджетный диапазон</span></li>
    </ul>
  </section>
</main>
{site_footer()}
"""
    return shell("NMBOT — Jivo runtime", body, description="Короткий публичный обзор Jivo transport, selector и отдельных V0/V2/V3 runtime flows NMBOT.", canonical=page_url())


def build_versions_page() -> str:
    body = f"""
<header role="banner">
  <span class="eyebrow" style="color:#9de7e7">Паспорт версий</span>
  <h1>V0, V2 и V3 — разные договорённости с клиентом.</h1>
  <p>Сравнение помогает не смешивать контракты, release gates и доказательства качества между версиями.</p>
</header>
<main id="content">
  <nav aria-label="Основная навигация"><a href="/index.html">← Все сервисы</a><a href="index.html">NMBOT overview</a><a href="#compare">Сравнение</a><a href="#passport">Граница</a><a href="architecture-v2.html">Архитектура решений</a></nav>
  <section class="card">
    <span class="eyebrow">Общее для всех версий</span>
    <h2>Один transport — разные семантики</h2>
    <p class="muted">Jivo transport, per-session lock, deduplication и terminal BOT_MESSAGE общие. Selector выбирает версию до обработки сообщения; дальше выбранная версия владеет диалоговым смыслом и клиентским текстом.</p>
  </section>
  <section id="compare" class="grid" aria-label="Сравнение версий">
    <article class="version v0"><span class="eyebrow">V0 · Валерия</span><h2>Brief → answer</h2><p>Два model-facing шага: scenario_search строит проверенный brief, затем answer пишет строго из него. V0 не публикует V2/V3 composer.</p><p><span class="tag">свой namespace</span><span class="tag">свои prompts</span><span class="tag">свой gate</span></p></article>
    <article class="version v2"><span class="eyebrow">V2 · Ирина</span><h2>Plan → renderer</h2><p>Typed state и canonical cards превращают intent в исполнимый ResponsePlan. Deterministic renderer — обязательная безопасная основа.</p><p><span class="tag">ResponsePlan</span><span class="tag">OptionCard</span><span class="tag">composer optional</span></p></article>
    <article class="version v3"><span class="eyebrow">V3 · Светлана</span><h2>IntentPlanV3 → transition</h2><p>Использует typed runtime V2, но добавляет строгий semantic contract: один goal, viewpoint, constraints и requested facts до механического transition.</p><p><span class="tag">IntentPlanV3</span><span class="tag">typed validation</span><span class="tag">separate mode</span></p></article>
  </section>
  <section id="passport" class="card"><span class="eyebrow">Composer boundary</span><h2>V2 и V3 публикуют model prose только после валидации</h2><p class="muted">Composer не владеет route, recipe, option order, anchor или CTA. Любая ошибка оставляет заранее собранный deterministic ответ.</p><p><a class="button" href="index.html#flow">Вернуться к схеме потоков</a></p></section>
</main>
{site_footer()}
"""
    return shell("NMBOT — V0/V2/V3", body, description="Публичный паспорт различий V0, V2 и V3 без внутренних команд и operational details.", canonical=page_url("versions.html"))


def build_architecture_v2_page() -> str:
    blocks = [
        ("State / Memory", "Хранит текущий контекст диалога: параметры, видимые варианты, выбранный ЖК и безопасные флаги."),
        ("Intent Planner", "Понимает смысл пользовательского сообщения, но не пишет клиентский ответ."),
        ("Search Decision", "Решает, нужен ли новый инструментальный поиск или достаточно текущих подтверждённых карточек."),
        ("Normalizer", "Превращает найденное в canonical cards и отделяет exact от near-only."),
        ("Decision Context", "Сжимает ситуацию в короткую безопасную карточку для следующего решения."),
        ("Action Resolver", "Проверяет, можно ли выполнить выбранное действие без опасной подмены сценария."),
        ("Presenter / Composer", "Формулирует ответ живым языком только по разрешённым данным."),
        ("Safety Validator", "Финально проверяет: нет ли неподтверждённых ЖК, чисел, контактов или технического мусора."),
    ]
    block_details = "".join(f"<details><summary>{html.escape(title)}</summary><p class='muted'>{html.escape(text)}</p></details>" for title, text in blocks)
    body = f"""
<header role="banner">
  <span class="eyebrow" style="color:#9de7e7">Архитектура решений</span>
  <h1>Как typed runtime решает, что можно ответить.</h1>
  <p>Без публикации сырого ТЗ: только короткая безопасная схема ролей между intent, search, cards, ResponsePlan и validator.</p>
</header>
<main id="content">
  <nav aria-label="Основная навигация"><a href="/index.html">← Все сервисы</a><a href="index.html">NMBOT overview</a><a href="versions.html">Паспорт версий</a><a href="resources.html">Ресурсы</a></nav>
  <section class="card">
    <h2>Зачем эта архитектура</h2>
    <p class="muted">Главная идея простая: LLM не должна сама разбирать сырой search payload и помнить все запреты. Между данными и ответом стоят typed cards, Action Resolver и Safety Validator.</p>
  </section>
  <section class="card">
    <h2>Безопасная цепочка</h2>
    <div class="flow-row"><div class="node">Intent</div><div class="node">Search decision</div><div class="node">Canonical cards</div><div class="node">ResponsePlan</div><div class="node accent">Validated answer</div></div>
  </section>
  <section class="card"><h2>Роли блоков</h2>{block_details}</section>
</main>
{site_footer()}
"""
    return shell("NMBOT — Архитектура решений", body, description="Публичная безопасная схема decision architecture для typed NMBOT runtime.", canonical=page_url("architecture-v2.html"))


def grouped_docs() -> dict[str, list[SourceDoc]]:
    groups: dict[str, list[SourceDoc]] = {}
    for doc in DOCS:
        groups.setdefault(doc.group, []).append(doc)
    return groups


def build_resources_page() -> str:
    sections: list[str] = []
    for group, docs in grouped_docs().items():
        cards = "".join(
            f"<article class='card'><h3>{html.escape(doc.title)}</h3><p class='muted'>{html.escape(doc.note)}</p><p><span class='tag'>{html.escape(doc.group)}</span></p></article>"
            for doc in docs
        )
        sections.append(f"<section aria-labelledby='{html.escape(group)}'><h2 id='{html.escape(group)}'>{html.escape(group)}</h2><div class='grid'>{cards}</div></section>")
    body = f"""
<header role="banner">
  <span class="eyebrow" style="color:#9de7e7">Resources</span>
  <h1>Каталог источников без сырого содержимого.</h1>
  <p>Эта страница перечисляет назначение внутренних документов, но не публикует их тексты, prompt bodies, команды, trace или operational details.</p>
</header>
<main id="content">
  <nav aria-label="Основная навигация"><a href="/index.html">← Все сервисы</a><a href="index.html">NMBOT overview</a><a href="versions.html">Паспорт версий</a><a href="architecture-v2.html">Архитектура решений</a></nav>
  {''.join(sections)}
</main>
{site_footer()}
"""
    return shell("NMBOT — Resources", body, description="Безопасный каталог внутренних NMBOT источников без публикации raw docs, prompts or traces.", canonical=page_url("resources.html"))


def build_archive_page() -> str:
    body = f"""
<header role="banner">
  <span class="eyebrow" style="color:#9de7e7">Archive</span>
  <h1>Исторические материалы без operational деталей.</h1>
  <p>Legacy-схемы и история оставлены тихо, отдельно от primary navigation, чтобы не смешивать текущий Jivo runtime с архивом.</p>
</header>
<main id="content">
  <nav aria-label="Основная навигация"><a href="/index.html">← Все сервисы</a><a href="index.html">NMBOT overview</a><a href="versions.html">Паспорт версий</a><a href="resources.html">Ресурсы</a></nav>
  <section class="grid">
    <article class="card"><h2>Legacy scenario map</h2><p class="muted">Историческая блок-схема не является текущим source of truth для Jivo selector. Публично оставлен только безопасный summary, без raw prompt text и operational snippets.</p></article>
    <article class="card"><h2>Dialogue history</h2><p class="muted">История не загружается автоматически. Если нужен sanitized snapshot, откройте отдельную страницу и нажмите кнопку загрузки.</p><p><a class="button" href="history.html">Открыть историю</a></p></article>
  </section>
</main>
{site_footer()}
"""
    return shell("NMBOT — Archive", body, description="Тихий архив публичного NMBOT overview без legacy links в основной навигации.", canonical=page_url("archive.html"))


def build_history_page() -> str:
    body = f"""
<header role="banner">
  <span class="eyebrow" style="color:#9de7e7">Sanitized history</span>
  <h1>История загружается только по кнопке.</h1>
  <p>Страница делает один lazy-запрос к history.json только после явного действия пользователя. Постоянного polling здесь нет.</p>
</header>
<main id="content">
  <nav aria-label="Основная навигация"><a href="/index.html">← Все сервисы</a><a href="index.html">NMBOT overview</a><a href="archive.html">Archive</a></nav>
  <section class="card" aria-labelledby="history-title">
    <h2 id="history-title">Sanitized dialogue snapshot</h2>
    <p class="muted">Если записей нет, список останется скрытым. Raw trace, logs, prompts and operational payloads здесь не публикуются.</p>
    <p><button class="button" type="button" id="history-load">Загрузить один раз</button> <span id="history-status" class="muted">ожидает действия</span></p>
    <div id="history-list" hidden></div>
  </section>
</main>
{site_footer()}
<script>
const historyButton = document.getElementById('history-load');
const historyList = document.getElementById('history-list');
const historyStatus = document.getElementById('history-status');
function esc(value) {{
  return String(value ?? '').replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
}}
function renderHistory(payload) {{
  const items = Array.isArray(payload.items) ? payload.items : [];
  historyStatus.textContent = `обновлено: ${{payload.generated_at || '—'}} · записей: ${{items.length}}`;
  if (!items.length) {{
    historyList.hidden = true;
    historyList.innerHTML = '';
    return;
  }}
  historyList.hidden = false;
  historyList.innerHTML = items.map((item, idx) => `
    <details>
      <summary>#${{idx + 1}} · ${{esc(item.ts)}} · ${{esc(item.dialog_id)}}</summary>
      <div style="padding:16px">
        <p><strong>Клиент:</strong><br>${{esc(item.user)}}</p>
        <p><strong>Бот:</strong><br>${{esc(item.bot)}}</p>
      </div>
    </details>
  `).join('');
}}
async function loadHistoryOnce() {{
  historyButton.disabled = true;
  historyStatus.textContent = 'загрузка…';
  try {{
    const response = await fetch('history.json?ts=' + Date.now(), {{cache:'no-store'}});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    renderHistory(await response.json());
  }} catch (error) {{
    historyStatus.textContent = 'не удалось загрузить безопасный снимок истории';
    historyButton.disabled = false;
  }}
}}
historyButton.addEventListener('click', loadHistoryOnce, {{once:true}});
</script>
"""
    return shell("NMBOT — History", body, description="Sanitized NMBOT dialogue history with one-shot lazy loading and no polling.", canonical=page_url("history.html"))


def build_map_archive_page() -> str:
    return build_archive_page()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    nmbot_dir = OUT / NMBOT_SLUG
    nmbot_dir.mkdir(parents=True, exist_ok=True)
    outputs = {
        OUT / "index.html": build_root_index(),
        nmbot_dir / "index.html": build_nmbot_index(),
        nmbot_dir / "versions.html": build_versions_page(),
        nmbot_dir / "architecture-v2.html": build_architecture_v2_page(),
        nmbot_dir / "resources.html": build_resources_page(),
        nmbot_dir / "archive.html": build_archive_page(),
        nmbot_dir / "history.html": build_history_page(),
        nmbot_dir / "map.html": build_map_archive_page(),
    }
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")
        print(f"built {path}")


if __name__ == "__main__":
    main()
