# NMBot V6

Clean V6-only source root for the Jivo real-estate assistant.

The same immutable V6 artifact can run in two isolated profiles:

- **TEST** — `[TEST]` greeting and CRM physically disabled;
- **PROD** — normal greeting; CRM still requires its explicit private control file.

The release control plane keeps independent TEST/PROD A/B slots, verifies an inactive
slot before an atomic route switch, and retains the previous warm slot for rollback.
Copying or promoting an artifact never activates PROD automatically.

See `docs/CURRENT_ARCHITECTURE.md` and `docs/NMBOT_RUNBOOK.md`.
