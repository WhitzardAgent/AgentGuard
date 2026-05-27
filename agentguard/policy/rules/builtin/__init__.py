"""Built-in static rules bundled with AgentGuard.

Loaded by default. Disable with `Guard(builtin_rules=False)` or override
individual rules by registering a rule with the same `rule_id`.
"""

from __future__ import annotations

from pathlib import Path

BUILTIN_RULES_DIR: Path = Path(__file__).parent
