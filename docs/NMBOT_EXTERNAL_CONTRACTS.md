# External contracts

## Jivo

Inbound `CLIENT_MESSAGE` goes to bridge then V6 API. Normal outbound is `BOT_MESSAGE`;
explicit operator handoff is `INVITE_AGENT`. Provider and bridge tokens are checked before
work starts. Client-visible PROD text passes the V6 egress guard.

## Gateway

The V6 simple gateway uses exactly two prompts and the configured private gateway client.
Provider calls are never part of local release verification unless separately authorized.
The gateway create-task response may return `id` as a positive integer or a safe string;
the client normalizes either wire form to a string before polling `/status` and `/result`.
Missing, zero, negative, fractional, boolean or unsafe IDs are rejected. This is a wire
compatibility rule, not an MCP tool or prompt change.

## Callback and CRM

Phone capture writes a private durable outbox. TEST can never enable CRM. PROD CRM still
requires an explicit private control file. The callback worker is deterministic-only;
Sheet and CRM delivery states remain independent.

## Privacy

Dialogue journals contain hashes, lengths and bounded codes, not raw dialogue/contact
data. Release journals contain identities, checks and transitions, never customer data.
