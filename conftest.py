"""Pytest bootstrap: make the new src/ layout importable from repo root."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent

# Import roots: client package, shared, server backend, and repo root (skills/).
_PATHS = [
    _ROOT / "src" / "client" / "python",  # -> agentguard
    _ROOT / "src",                          # -> shared
    _ROOT / "src" / "server",               # -> backend
    _ROOT,                                  # -> skills
]

for _p in _PATHS:
    sp = str(_p)
    if sp not in sys.path:
        sys.path.insert(0, sp)
