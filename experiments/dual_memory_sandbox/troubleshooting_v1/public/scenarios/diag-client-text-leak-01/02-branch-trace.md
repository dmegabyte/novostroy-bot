# 02 — Branch trace

Evidence IDs: `leak.e4`, `leak.e5`, `leak.e6`.

- `leak.e4`: Trace label: `OPERATOR_HANDOFF`.
- `leak.e5`: The unsafe text came from planner/decision metadata associated with
  the handoff branch.
- `leak.e6`: The presentation layer appended that internal reason into text that
  reached the client.
