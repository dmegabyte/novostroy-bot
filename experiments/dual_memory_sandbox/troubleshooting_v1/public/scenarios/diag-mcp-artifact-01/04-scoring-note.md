# 04 — Scoring note

Evidence IDs: `mcp.e10`, `mcp.e11`, `mcp.e12`.

- `mcp.e10`: The missing-after-request warning means no received facts payload was
  available to score as content.
- `mcp.e11`: The correct score outcome for such a row is zero because the artifact
  sequence failed before a usable search payload.
- `mcp.e12`: Do not invent provider failure or semantic empty-search interpretation;
  diagnose the artifact/pipeline contract state shown by the ordered evidence.
