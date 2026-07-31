# 01 — Client observation

Evidence IDs: `leak.e1`, `leak.e2`, `leak.e3`.

- `leak.e1`: A real client-visible message contained an internal state-like token
  rendered as ordinary text.
- `leak.e2`: The surrounding branch was an operator-handoff-style response, not a
  search result card.
- `leak.e3`: The user-facing context still had a safe canonical selected project
  name available; it did not require exposing planner internals.

Tempting false conclusion: the internal token means MCP/search returned a bad or
empty answer.
