# 01 — CLI sequence

Evidence IDs: `mcp.e1`, `mcp.e2`, `mcp.e3`.

- `mcp.e1`: The CLI printed a redacted `MCP request` block.
- `mcp.e2`: The CLI exited nonzero before any `Search Facts` block appeared.
- `mcp.e3`: No compact facts payload was observed after the request block.

Tempting false conclusion: because an MCP-shaped object exists later, MCP/search
must have returned an empty or semantically bad result.
