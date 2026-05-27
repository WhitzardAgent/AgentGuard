"""Pre-computed graph feature keys used on the hot path.

Each `EXISTS_PATH(...)` expression in a rule is lowered to a feature key that
the context collector populates asynchronously; the fast evaluator only reads
a boolean/float.
"""

from __future__ import annotations


class FeatureKey:
    @staticmethod
    def exists_path(rule_id: str) -> str:
        return f"graph.exists_path.{rule_id}"

    @staticmethod
    def recent_tool(tool_name: str) -> str:
        return f"recent.tool.{tool_name}"

    @staticmethod
    def session_label(label: str) -> str:
        return f"session.label.{label}"
