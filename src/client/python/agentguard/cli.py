"""AgentGuard command-line interface."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from shared.rules.dsl_compat import parse_legacy_rules
from shared.rules.loader import load_rules_dir as load_shared_rules_dir
from shared.rules.loader import load_rules_file as load_shared_rules_file
from shared.utils.errors import PolicyError


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
    if not _apply_server_policies(getattr(args, "policy", []) or []):
        return 2
    uvicorn.run("backend.api.app:app", host=args.host, port=args.port)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    target = Path(args.path)
    if target.is_dir():
        files = sorted(target.glob("*.rules"))
        if not files:
            print(f"no .rules files found in directory: {target}", file=sys.stderr)
            return 2
        ok = True
        total_rules = 0
        for file in files:
            file_ok, rule_count = _check_rules_file(file)
            ok = ok and file_ok
            total_rules += rule_count
        if ok:
            print(f"ok: validated {len(files)} file(s), {total_rules} rule block(s)")
            return 0
        return 1

    if target.suffix.lower() == ".rules":
        file_ok, _ = _check_rules_file(target)
        return 0 if file_ok else 1

    try:
        rules = load_shared_rules_file(target)
    except PolicyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"ok: {target} ({len(rules)} rules)")
    return 0


def _cmd_example(args: argparse.Namespace) -> int:
    import runpy

    script = Path("examples") / f"{args.name}.py"
    if not script.exists():
        print(f"example not found: {script}", file=sys.stderr)
        return 2
    runpy.run_path(str(script), run_name="__main__")
    return 0


def _check_rules_file(path: Path) -> tuple[bool, int]:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read rule file {path}: {exc}", file=sys.stderr)
        return False, 0

    _, report = parse_legacy_rules(source)
    if report.ok:
        print(f"ok: {path} ({report.rule_count} rule block(s))")
        return True, report.rule_count

    for error in report.errors:
        print(f"error: {path}: {error['message']}", file=sys.stderr)
    return False, report.rule_count


def _apply_server_policies(paths: list[str]) -> bool:
    if not paths:
        return True
    try:
        from backend.app_state import get_manager  # noqa: PLC0415
    except ImportError as exc:
        print(f"server runtime unavailable: {exc}", file=sys.stderr)
        return False

    rules = []
    try:
        for item in paths:
            path = Path(item)
            if path.is_dir():
                rules.extend(load_shared_rules_dir(path))
            else:
                rules.extend(load_shared_rules_file(path))
    except PolicyError as exc:
        print(str(exc), file=sys.stderr)
        return False

    get_manager().policy.store.set_rules(rules)
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentguard")
    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser("check", help="validate a legacy .rules file or directory")
    check.add_argument("path")
    check.set_defaults(func=_cmd_check)

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
    server.add_argument("--policy", action="append", default=[])
    server.set_defaults(func=_cmd_server)

    serve = sub.add_parser("serve", help="run the server (README compatibility alias)")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--policy", action="append", default=[])
    serve.set_defaults(func=_cmd_server)

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
