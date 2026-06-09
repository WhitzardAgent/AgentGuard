"""Developer skill demo: turn natural language into a policy rule, then lint it."""
from __future__ import annotations

import json

import _bootstrap  # noqa: F401

from skills.base import SkillInput
from skills.registry import get_registry


def main() -> None:
    reg = get_registry()

    writer = reg.get("dsl_writer")
    out = writer.run(
        SkillInput(instruction="deny external send when the payload contains a secret")
    )
    rules = out.result.get("rules", [])
    print("generated rule:")
    print(json.dumps(rules[0] if rules else None, indent=2, ensure_ascii=False))

    linter = reg.get("rule_linter")
    lint = linter.run(SkillInput(data={"rules": rules}))
    print("\nlint:", "ok" if lint.success else "issues", lint.result.get("issues"))


if __name__ == "__main__":
    main()
