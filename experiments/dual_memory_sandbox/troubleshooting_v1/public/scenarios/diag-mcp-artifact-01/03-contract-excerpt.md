# 03 — Contract excerpt

Evidence IDs: `mcp.e7`, `mcp.e8`, `mcp.e9`.

- `mcp.e7`: `mcp_request` is the request contract: what the system asked from
  search.
- `mcp.e8`: `mcp_response` is only the actual search facts payload, containing
  facts-like response fields after the facts block.
- `mcp.e9`: A request by itself cannot be treated as the response; if the CLI stops
  before facts are available, the response field must remain an empty object.
