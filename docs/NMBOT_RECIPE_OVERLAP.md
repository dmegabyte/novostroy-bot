# NMBOT recipe semantic overlap

`nmbot_v2/scenario_recipes.py` is the executable recipe registry. This tool helps review where recipes are structurally or semantically close without changing that registry.

## Run manually

```bash
python3 scripts/nmbot.py recipes overlap --human
python3 scripts/nmbot.py recipes overlap --json --threshold 0.82 --top 20
python3 scripts/nmbot.py recipes pair selected_financing current_options_financing --human
python3 scripts/nmbot.py recipes explain selected_financing current_options_financing --human
python3 scripts/nmbot_recipe_overlap.py --explain selected_financing current_options_financing --json
```

The command builds a deterministic passport for every recipe: stage, viewpoint, scope, fact priority, benefit keys and text, forbidden inferences, CTA, reply contract and composition mode. It obtains one batched embedding response from local Ollama using `nomic-embed-text:latest`, then calculates cosine similarity and shows exact field intersections beside every candidate pair.

## Boundaries

- Ollama is called only when this explicit command runs, and only at `localhost` or `127.0.0.1` over HTTP.
- The report contains `needs_review` candidates, not defects, deduplication instructions, refactoring orders, or production claims.
- It never changes recipes, runs the bot runtime, calls Jivo/VPS/external providers, reads environment values, writes a cache, or runs as part of `nmbot check` or CI.
- If local Ollama is unavailable or returns an invalid embedding shape, the command fails honestly with a non-zero code. It does not fabricate a score.

## How to read a pair

Use the semantic score to locate pairs worth reading. Then compare the explicit `exact_overlap` fields and the full source recipes. Similar vectors may still represent deliberately distinct scenarios: for example, rental and investment can share readiness facts while having different forbidden inferences.

The registry and its test matrix remain authoritative. This tool is a navigation and review aid only.

## Inspect one pair without embeddings

`recipes pair RECIPE_A RECIPE_B` builds a deterministic local Markdown card. It does not call Ollama. The card contains both passports, exact common fields, field-level differences, and a best-effort textual list of local test files mentioning both IDs.

The test-file list is navigation only: it does not prove that a test covers the pair's behaviour.

## Explain one pair without embeddings

`recipes explain RECIPE_A RECIPE_B` builds a deterministic local Markdown/JSON review card from the same pair report. It does not call Ollama, the network, subprocesses, Jivo, VPS, model APIs, or the bot runtime.

The explain card contains:

- recipe IDs and source registry;
- a plain-language conclusion based only on static recipe facts;
- shared static facts: stage, viewpoint, scope, forbidden rules, reply contract, and CTA equality;
- concrete differences to inspect: stage, scope, CTA, and fact priority;
- a manual checklist for deciding “keep separate” versus “assess consolidation”;
- local textual test references explicitly labelled as navigation only;
- candidate-only boundaries.

The explain card must not be read as a merge recommendation, defect report, deletion instruction, production proof, or automatic refactoring instruction. Unknown recipe IDs fail before any embedding client can be instantiated.

Sources: `nmbot_v2/scenario_recipes.py`; `tests/test_nmbot_v2_recipe_transition_matrix.py`; official Ollama embedding API documentation (`POST /api/embed`).
