# Nmbot PROD live dialogue monitor — approved plan

**Status:** planning only; not implemented, not deployed, and not proof of
current production behavior.

**Decision date:** 2026-08-23. The future monitor targets contour `primary`;
its public traffic role still requires fresh live verification before release.

## Goal and approved contract

Provide a private, read-only online view of new PROD Jivo dialogues and notify
the owner on Android and desktop Chrome, including when the tab is closed.
Replies and operator actions remain in Jivo.

Target URL:

```text
https://193.107.155.236/nmbot-live
```

The existing application on port `8766` is outside this change and must remain
untouched.

- One owner account; all explicitly enrolled owner devices may subscribe.
- Password plus Authenticator TOTP; secure session lifetime: 7 days.
- New client messages appear in the feed and produce one replaceable ordinary
  notification per conversation.
- Ordinary push contains no client text, phone, email, or other PII.
- API/model errors, Jivo delivery failures, operator handoff needs, and a
  missing terminal response after 60 seconds produce a separate urgent state.
- The feed shows full new dialogue from activation onward; diagnostics are
  collapsed by default.
- A segment closes after 30 minutes of silence; later activity follows the
  implementation's documented segmentation rule.
- Events are durable and replayable after a dashboard/network outage.
- Full original text is field-encrypted and automatically purged after exactly
  30 days. Older history remains redacted and is labelled as such.
- A distinct closed-tab OS sound is not a reliable browser contract; urgent
  visual state is mandatory, while a separate sound may be used in the panel.

## Target architecture

```text
NMBot API + Jivo bridge owner points
        -> durable local event/outbox store
        -> projector/live-monitor service
        -> SSE feed + REST history + service worker/Web Push
```

For the expected load (under 20 new PROD dialogues/day), the first
implementation should use SQLite in WAL mode, not Kafka or another heavy event
bus. Suggested logical areas are append-only events, conversation read model,
push outbox, push subscriptions, sessions, and access audit.

Events are emitted at API and bridge owner points rather than reconstructed by
tailing logs:

- API: inbound client text before canonical redaction, bot result, handoff and
  processing error;
- bridge: terminal Jivo delivery acceptance or failure, correlated with the
  trace and conversation segment.

Monitor writes must fail open: a monitor failure must not block the client
response, and only a bounded safe operational error may be recorded.

The terminal timer is cleared only by correlated terminal
`BOT_MESSAGE`/`INVITE_AGENT` delivery acceptance from the bridge. API response
preparation alone is not delivery proof. The existing invariant of exactly one
terminal outcome per incoming turn remains unchanged.

## Security, privacy and HTTPS

- Store full text/PII with authenticated field encryption; keep the key outside
  the database, source tree and logs.
- Keep database and service files private; decrypt only after authorization.
- Do not place raw provider IDs, client identifiers or PII in trace IDs.
- Audit reads and security events without copying message text into the audit
  record.
- Protect login with password KDF, TOTP, Secure/HttpOnly/SameSite cookie, CSRF
  protection, rate limiting, and device/subscription revocation.
- Push payloads, telemetry and ordinary logs remain privacy-safe.
- Bind the future service to loopback. A separate TLS ingress owns port 443;
  port 8766 remains with its existing dashboard.
- Before release, verify public reachability and ACME client support for the
  short-lived Let's Encrypt IP certificate profile. Renewal must be automated
  and monitored.

## Delivery phases and release gates

1. Repair and verify local Git object integrity.
2. Take and compare a fresh PROD source snapshot; implement only in an isolated
   worktree.
3. Build event storage, encryption, API/bridge hooks, projector, SSE/REST UI,
   auth/TOTP, push outbox/service worker, retention job and deployment assets.
4. Run offline tests for deduplication, ordering, replay, segmentation,
   terminal timeout, encryption, PII exclusion, TTL deletion, auth, SSE
   `Last-Event-ID`, push replacement/retry, and fail-open behavior.
5. Deploy event capture in shadow/disabled-public mode and verify correlation.
6. Separately enable the authenticated service, HTTPS and device enrollment.
7. After explicit release approval, perform fresh contour, HTTPS, auth,
   correlated Jivo terminal-delivery, push and retention checks. Stop at the
   first failure.

Local checks do not prove PROD behavior. Any future model/provider/Jivo test
must identify contour `primary` first and obtain the required separate test
authorization.

## Open ownership and preflight items

- Assign owners for monitor operation, encryption secrets, retention and
  certificate-renewal alarms; the operations map currently leaves these `TBD`.
- Document the 30-day full-text retention decision and responsible owner.
- Verify firewall/reachability on ports 80/443 and concrete ACME support.
- Reconfirm `primary` release identity and public route immediately before
  production rollout.

This document records an approved design decision, not an implementation
receipt. Historical planning context is also recorded in NotebookLM note
`756494791dcf`.
