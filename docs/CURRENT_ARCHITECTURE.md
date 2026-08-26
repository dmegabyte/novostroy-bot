# Current architecture

There is one runtime: V6. `scripts/nmbot_api_server.py` delegates to the thin V6 API,
which calls the active simple runtime package and two V6 prompts.

Jivo traffic enters the bridge. The bridge authenticates the request, resolves a strict
loopback active-route file, calls one prepared V6 slot, applies the egress guard and sends
one terminal Jivo event. Invalid routes fail closed with a safe terminal answer.

TEST and PROD use the same immutable artifact but have independent state, journals,
outboxes, env files, A/B slots and route files. TEST CRM delivery is disabled in code.

The registry is immutable and append-only. The controller prepares an inactive slot,
checks exact runtime/profile/release identity, then atomically switches the route. The
previous slot remains available for immediate rollback.
