# 01 — Visible symptoms

Evidence IDs: `composer.e1`, `composer.e2`, `composer.e3`.

- `composer.e1`: In one version, a model-written first shortlist kept project
  names and scenario-style benefits, but useful factual card fields such as
  location and price were absent from the client-facing copy.
- `composer.e2`: A separate version twice returned an operator-handoff-like answer
  instead of the expected rental shortlist; its failure marker was strict JSON
  malformedness upstream of answer text.
- `composer.e3`: Two later-version rental attempts had parse/timeout/fallback
  symptoms before a final answer could be composed.

Tempting false conclusion: because the symptoms appeared near the same rollout,
all failures must share one deterministic composer cause.
