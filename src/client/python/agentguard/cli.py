"""AgentGuard command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _run_skill(name: str, input_data: dict[str, Any]) -> int:
    from skills.base import SkillInput
    from skills.registry import get_registry

    skill = get_registry().get(name)
    if skill is None:
        print(f"unknown skill: {name}", file=sys.stderr)
        return 2
    out = skill.run(
        SkillInput(
            instruction=input_data.get("instruction"),
            data=input_data.get("data") or {},
            context=input_data.get("context") or {},
        )
    )
    print(json.dumps(out.to_dict(), indent=2, ensure_ascii=False))
    return 0 if out.success else 1


def _cmd_skill(args: argparse.Namespace) -> int:
    if args.skill_action == "run":
        return _run_skill(args.name, {"instruction": args.input})
    if args.skill_action == "lint":
        return _run_skill("rule_linter", {"data": {"rules": _as_rules(_load_json(args.file))}})
    if args.skill_action == "explain":
        return _run_skill("policy_explainer", {"data": {"rules": _as_rules(_load_json(args.file))}})
    if args.skill_action == "test":
        return _run_skill(
            "rule_tester",
            {"data": {"rule": _load_json(args.rule), "event": _load_json(args.event)}},
        )
    print("unknown skill action", file=sys.stderr)
    return 2


def _as_rules(data: Any) -> list:
    if isinstance(data, dict):
        return data.get("rules", [data])
    return data if isinstance(data, list) else [data]


def _cmd_server(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn not installed; install the 'server' extra.", file=sys.stderr)
        return 2
    uvicorn.run("backend.api.app:app", host=args.host, port=args.port)
    return 0


def _cmd_example(args: argparse.Namespace) -> int:
    import runpy

    script = Path("examples") / f"{args.name}.py"
    if not script.exists():
        print(f"example not found: {script}", file=sys.stderr)
        return 2
    runpy.run_path(str(script), run_name="__main__")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentguard")
    sub = parser.add_subparsers(dest="command", required=True)

    skill = sub.add_parser("skill", help="run developer skills")
    skill_sub = skill.add_subparsers(dest="skill_action", required=True)
    run_p = skill_sub.add_parser("run")
    run_p.add_argument("name")
    run_p.add_argument("--input", default="")
    lint_p = skill_sub.add_parser("lint")
    lint_p.add_argument("file")
    explain_p = skill_sub.add_parser("explain")
    explain_p.add_argument("file")
    test_p = skill_sub.add_parser("test")
    test_p.add_argument("--rule", required=True)
    test_p.add_argument("--event", required=True)
    skill.set_defaults(func=_cmd_skill)

    server = sub.add_parser("server", help="run the server")
    server.add_argument("server_action", choices=["start"])
    server.add_argument("--host", default="0.0.0.0")
    server.add_argument("--port", type=int, default=8000)
    server.set_defaults(func=_cmd_server)

    example = sub.add_parser("example", help="run an example")
    example.add_argument("name")
    example.set_defaults(func=_cmd_example)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
