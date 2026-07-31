# Isolated nmbot search prompt eval

This directory is a safe V2 MCP search test environment. The runner reads the
full `prompts/v2_search_mcp.txt` as the single source of truth and does not
modify it. The earlier compact candidate remains under `prompts/` only as a
failed baseline artifact.

## Static check, no network

```bash
python3 eval/nmbot-search-prompt/run_eval.py --fixture-only
python3 eval/nmbot-search-prompt/run_eval.py --fixture-only --case family
```

## Live warning

Running without `--fixture-only` uses the live gateway/OpenRouter/MCP path and can spend money:

```bash
python3 eval/nmbot-search-prompt/run_eval.py --case family --timeout 90
python3 eval/nmbot-search-prompt/run_eval.py --all --timeout 90 --output /tmp/nmbot-search-prompt-result.json
```

Use live mode only when explicitly allowed. The model is fixed to `google/gemini-3.1-flash-lite-preview`, MCP alias is fixed to `novostroym`, and payload stage is fixed to `main_search`.

## First-failure rule

Cases run sequentially. `--all` stops immediately after the first failed case and returns a non-zero exit code. Do not continue a batch blindly after a first failure; inspect that single result first.

## Eval-only bounded enrichment

Live eval mode keeps the initial gateway request, then validates the parsed and
normalized search output through the offline helper
`nmbot_v2.search_enrichment.validate_with_bounded_enrichment`. This is strictly
inside the isolated eval contour and does not change production/deploy behavior
or the production prompt.

For recoverable hard-evidence gaps, the helper may run one broad recovery
request and then exact-card enrichment for at most five options
(`MAX_ENRICHMENT_OPTIONS = 5`). The maximum recoverable call chain is therefore:

```text
initial search + broad recovery + up to 5 exact-card requests = max 7 gateway calls
```

Initial gateway or JSON parse failures do not invoke enrichment. The final
candidate usefulness gate still runs after enrichment, so a response with zero
confirmed `facts` + `near` candidates still fails even if the helper validation
itself returned a schema-valid result.

## Expected output

The runner prints bounded JSON only. It reports `contract_ok` separately from
`found_options`. Every current case requires at least one candidate in `facts`
or `near`; an empty but schema-valid response therefore fails with
`insufficient_candidates`. The output also includes counts, bounded names and
missing items, safe gateway metadata and elapsed time. It does not dump secrets
or full upstream payloads. Enrichment telemetry is also bounded to safe scalar
status fields and short list summaries; it never includes raw requests,
responses or credentials.
