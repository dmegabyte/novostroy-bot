# NMBot V6-only root

This repository contains one bot runtime: V6. Do not add another `nmbot_v<digits>`
package, selector, fallback, prompt set, or compatibility adapter.

## Safety

- TEST and PROD are exact, independent profiles of the same immutable artifact.
- TEST CRM delivery is always disabled in code.
- A release ID is immutable. Never overwrite an existing artifact or extracted release.
- Prepare and verify an inactive A/B slot before changing a route. Keep the previous
  slot warm for rollback.
- Local files and tests do not prove production state. Any live check, release,
  activation, rollback, or deploy requires its own explicit authorization.
- Never print or commit secrets, dotenv values, raw dialogue, phone numbers, or Jivo
  payloads.
- Do not run eval or paid model/provider calls without explicit authorization.

## Verification

Use the smallest focused local check that proves the changed owner contract. Before a
release, build the exact artifact, run isolated startup admission, and verify identity,
profile, routes, and hashes.
