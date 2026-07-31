# 03 — Timeline notes

Evidence IDs: `composer.e7`, `composer.e8`, `composer.e9`.

- `composer.e7`: A similar gateway timeout was observed before the later rollout
  window.
- `composer.e8`: After rollback, the exact smoke request succeeded in all three
  versions on the first try.
- `composer.e9`: The bridge and API health checks were healthy after rollback, and
  fresh non-validation error count was zero in the smoke window.
