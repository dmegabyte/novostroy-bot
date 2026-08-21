"""V6-only facade for the shared dialogue journal.

The shared journal retains optional legacy sanitizers. During its import this
facade makes those retired packages unavailable, selecting the journal's own
standalone fallback without loading V0-V5 modules.
"""

from __future__ import annotations

import importlib.abc
import sys


class _RetiredRuntimeBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
        if fullname.split(".", 1)[0] in {"nmbot_v0", "nmbot_v1", "nmbot_v2", "nmbot_v3", "nmbot_v4", "nmbot_v5"}:
            raise ModuleNotFoundError(f"retired runtime blocked: {fullname}", name=fullname)
        return None


_blocker = _RetiredRuntimeBlocker()
sys.meta_path.insert(0, _blocker)
try:
    from dialogue_journal import append_event
finally:
    sys.meta_path.remove(_blocker)

__all__ = ["append_event"]
