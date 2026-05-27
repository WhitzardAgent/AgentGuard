"""``python -m agentguard`` — CLI entry point.

Sub-commands
============

``serve``
    Start the HTTP runtime server. Supports both the synchronous Pipeline
    (``--runtime-mode sync``, default) and the asynchronous actor mesh
    (``--runtime-mode async``) with tunable loop parameters.

``validate``
    Parse + compile policy file(s) without serving. Exits non-zero on
    syntax errors so it can gate CI / pre-commit hooks.

``check``
    Deep rule validation with rich, actionable diagnostics.  Goes beyond
    ``validate`` by reporting semantic warnings, metadata hints, trace-clause
    issues, enum value checks, and fix suggestions for every problem found.

``health``
    Probe ``GET /health`` on a running runtime.

``metrics``
    Probe ``GET /metrics`` on a running async runtime. Returns aggregate
    counters from DecisionLoop, AuditLoop, DynamicRuleLoop, ReviewLoop.

``eval``
    Submit a single JSON-encoded ``RuntimeEvent`` to a running runtime
    (or evaluate locally against a ``--policy`` file) and print the
    Decision JSON.

Examples
========

Synchronous server with built-in rules::

    python -m agentguard serve --port 38080 --policy rules/my_policy.rules

Async actor mesh with custom tunables::

    python -m agentguard serve --runtime-mode async \\
        --policy rules/prod.rules --api-key secret123 \\
        --review-timeout-s 300 --dynamic-risk-threshold 0.7 \\
        --dynamic-cooldown-s 30 --audit-flush-interval-s 10

Validate a policy file in CI::

    python -m agentguard validate rules/prod.rules

Deep-check a rule file with fix suggestions::

    python -m agentguard check rules/my_policy.rules
    python -m agentguard check --stdin < rules/my_policy.rules
    python -m agentguard check --json  rules/my_policy.rules   # machine-readable

Probe a running runtime::

    python -m agentguard health  --url http://runtime:38080 --api-key secret
    python -m agentguard metrics --url http://runtime:38080 --api-key secret

Evaluate a single event locally without spinning up a server::

    python -m agentguard eval --policy rules/prod.rules --event sample.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_local_env_file(path: Path | None = None) -> None:
    """Load a simple KEY=VALUE .env file without overriding existing env vars."""
    env_path = path or Path.cwd() / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("export "):
            if line.startswith("export "):
                line = line[len("export "):].strip()
            else:
                continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip("'").strip('"')
        os.environ[key] = value


def _parse_allowlist(spec: str) -> tuple[str, list[str]]:
    """Parse ``key=v1,v2,v3`` into ``("key", ["v1", "v2", "v3"])``."""
    if "=" not in spec:
        raise argparse.ArgumentTypeError(
            f"--allowlist expects KEY=VAL[,VAL...], got {spec!r}"
        )
    key, _, values = spec.partition("=")
    items = [v.strip() for v in values.split(",") if v.strip()]
    return key.strip(), items


def _http_get(url: str, *, api_key: str = "", timeout: float = 5.0) -> dict[str, Any]:
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["X-Api-Key"] = api_key
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _http_post(
    url: str, body: dict[str, Any], *, api_key: str = "", timeout: float = 10.0
) -> dict[str, Any]:
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-Api-Key"] = api_key
    payload = json.dumps(body).encode()
    req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: serve
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_serve(args: argparse.Namespace) -> None:
    from agentguard.runtime.server import AgentGuardServer

    allowlists: dict[str, list[str]] = {}
    for spec in args.allowlist or []:
        k, v = _parse_allowlist(spec)
        allowlists[k] = v

    policy: list[str] | None = args.policy or None
    server = AgentGuardServer.from_policy(
        policy_source=policy,
        builtin_rules=not args.no_builtin,
        mode=args.mode,
        api_key=args.api_key or None,
        allowlists=allowlists or None,
        runtime_mode=args.runtime_mode,
        rule_pack_config=args.rule_pack_config,
        state_cache_url=args.state_cache,
        postgres_url=args.postgres_url,
    )

    # Stash async tunables on the server so the lifespan picks them up
    # when it constructs AgentGuardRuntime.from_guard().
    if args.runtime_mode == "async":
        from agentguard.runtime.server import AgentGuardRuntime

        original_ensure = server._ensure_async_runtime

        async def ensure_with_tunables() -> AgentGuardRuntime:
            if server._async_runtime is None:
                server._async_runtime = AgentGuardRuntime.from_guard(
                    server._guard,
                    review_timeout_s=args.review_timeout_s,
                    dynamic_risk_threshold=args.dynamic_risk_threshold,
                    dynamic_cooldown_s=args.dynamic_cooldown_s,
                    audit_flush_interval_s=args.audit_flush_interval_s,
                )
            if not server._async_runtime.started:
                await server._async_runtime.start()
            return server._async_runtime

        server._ensure_async_runtime = ensure_with_tunables  # type: ignore[assignment]

    # ── file-watcher hot-reload ────────────────────────────────────────────
    if getattr(args, "watch", False) and policy:
        server.start_watcher(
            paths=list(policy),
            interval_s=getattr(args, "watch_interval", 5.0),
            on_reload=lambda n: print(f"  [watcher] reloaded {n} rules"),
        )

    n = len(server.guard.active_rules())
    print(
        f"AgentGuard Runtime  mode={args.mode}  runtime={args.runtime_mode}  "
        f"rules={n}  http://{args.host}:{args.port}"
    )
    if args.runtime_mode == "async":
        print(
            f"  async tunables: review_timeout={args.review_timeout_s}s  "
            f"risk_threshold={args.dynamic_risk_threshold}  "
            f"cooldown={args.dynamic_cooldown_s}s  "
            f"audit_flush={args.audit_flush_interval_s}s"
        )
    if getattr(args, "watch", False) and policy:
        print(f"  watcher : enabled  paths={list(policy)}  interval={getattr(args, 'watch_interval', 5.0)}s")
    if args.api_key:
        print("  X-Api-Key  : required")
    if allowlists:
        print(f"  allowlists : {list(allowlists)}")

    server.serve(host=args.host, port=args.port, log_level=args.log_level)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: validate
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_validate(args: argparse.Namespace) -> int:
    from agentguard.policy.rules.loaders import load_rules

    paths: list[str] = list(args.policy or [])
    if not paths:
        print("validate: no --policy paths given", file=sys.stderr)
        return 2

    total = 0
    failed: list[tuple[str, str]] = []
    for p in paths:
        try:
            rules = load_rules(p)
            total += len(rules)
            print(f"  OK   {p}: {len(rules)} rules")
        except Exception as exc:
            failed.append((p, str(exc)))
            print(f"  FAIL {p}: {exc}", file=sys.stderr)

    print(f"\ntotal compiled: {total}  failed_files: {len(failed)}")
    return 0 if not failed else 1


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: check  (rich validator)
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_check(args: argparse.Namespace) -> int:
    from agentguard.policy.dsl.validator import validate_source, validate_file, ValidationReport
    import sys as _sys

    reports: list[ValidationReport] = []

    if getattr(args, "stdin", False):
        src = _sys.stdin.read()
        report = validate_source(src, source_file="<stdin>")
        reports.append(report)
    else:
        paths: list[str] = list(args.policy or [])
        if not paths:
            print("check: no paths given. Use  python -m agentguard check <file>  or  --stdin",
                  file=_sys.stderr)
            return 2
        import glob as _glob
        from pathlib import Path as _Path
        expanded: list[str] = []
        for p in paths:
            pp = _Path(p)
            if pp.is_dir():
                expanded.extend(str(f) for f in sorted(pp.glob("**/*.rules")))
            elif "*" in p or "?" in p:
                expanded.extend(sorted(_glob.glob(p, recursive=True)))
            else:
                expanded.append(p)
        for path in expanded:
            reports.append(validate_file(path))

    use_json = getattr(args, "json", False)
    no_color = getattr(args, "no_color", False) or not _sys.stdout.isatty()

    if use_json:
        print(json.dumps(
            [r.to_dict() for r in reports],
            indent=2, ensure_ascii=False,
        ))
    else:
        for r in reports:
            print(r.summary(color=not no_color))
            print()

    all_ok = all(r.ok for r in reports)
    return 0 if all_ok else 1




def _cmd_health(args: argparse.Namespace) -> int:
    try:
        body = _http_get(
            f"{args.url.rstrip('/')}/health",
            api_key=args.api_key or "",
            timeout=args.timeout,
        )
    except urllib.error.URLError as exc:
        print(f"unreachable: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0 if body.get("ok") else 1


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: metrics
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_metrics(args: argparse.Namespace) -> int:
    try:
        body = _http_get(
            f"{args.url.rstrip('/')}/metrics",
            api_key=args.api_key or "",
            timeout=args.timeout,
        )
    except urllib.error.URLError as exc:
        print(f"unreachable: {exc}", file=sys.stderr)
        return 2

    if body.get("metrics") is None:
        print(
            f"runtime_mode={body.get('runtime_mode')!r} — "
            "metrics are only available when the server runs with "
            "--runtime-mode async."
        )
        return 0

    print(json.dumps(body, indent=2, ensure_ascii=False))
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: eval
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_eval(args: argparse.Namespace) -> int:
    event_path = Path(args.event)
    if not event_path.is_file():
        print(f"event file not found: {event_path}", file=sys.stderr)
        return 2

    raw = event_path.read_text(encoding="utf-8")
    payload = json.loads(raw)

    if args.url:
        # Remote evaluation
        try:
            body = _http_post(
                f"{args.url.rstrip('/')}/v1/evaluate",
                payload,
                api_key=args.api_key or "",
                timeout=args.timeout,
            )
        except urllib.error.URLError as exc:
            print(f"unreachable: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(body, indent=2, ensure_ascii=False))
        return 0

    # Local evaluation against --policy
    if not args.policy:
        print(
            "eval: must specify either --url <runtime> or --policy <file>",
            file=sys.stderr,
        )
        return 2

    from agentguard.models.events import RuntimeEvent
    from agentguard.sdk.guard import Guard

    guard = Guard(
        policy_source=list(args.policy),
        builtin_rules=not args.no_builtin,
        mode=args.mode,
        llm_backend="env",
    )
    event = RuntimeEvent.model_validate(payload)
    decision = guard.pipeline.handle_attempt(event)
    print(json.dumps(
        {"ok": True, "decision": decision.model_dump(mode="json")},
        indent=2, ensure_ascii=False,
    ))
    guard.close()
    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Argparse wiring
# ─────────────────────────────────────────────────────────────────────────────

def _add_serve_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("serve", help="Start the HTTP runtime server")

    # Network
    p.add_argument("--host", default="0.0.0.0",
                   help="Bind host (default: 0.0.0.0)")
    p.add_argument("--port", type=int, default=38080,
                   help="Listen port (default: 38080)")
    p.add_argument("--log-level", default="info",
                   help="uvicorn log level (default: info)")

    # Policy
    p.add_argument("--policy", action="append", metavar="PATH",
                   help="Policy file or directory loaded into the default rule pack (repeatable)")
    p.add_argument("--no-builtin", action="store_true",
                   help="Disable built-in rules")
    p.add_argument("--allowlist", action="append", metavar="KEY=v1,v2,...",
                   help="Allowlist entries injected as features (repeatable)")
    p.add_argument("--rule-pack-config", default=None, metavar="PATH",
                   help="YAML/JSON file describing named rule packs and "
                        "agent ↔ pack bindings; loaded after --policy.")

    # Persistence backends (all optional — defaults are in-process)
    p.add_argument("--state-cache", default=None, metavar="URL",
                   help="Session-state cache backend. Defaults to in-memory; "
                        "pass redis://host:port/db to use Redis.")
    p.add_argument("--postgres-url", default=None, metavar="URL",
                   help="PostgreSQL DSN. When set, rules / agent bindings / "
                        "audit log / tool catalog are persisted there.")

    # Behaviour
    p.add_argument("--mode", default="enforce",
                   choices=["enforce", "monitor", "dry_run"],
                   help="Decision mode (default: enforce)")
    p.add_argument("--api-key", default="",
                   help="Require X-Api-Key header on /v1/evaluate")

    # Runtime mode + async tunables
    p.add_argument("--runtime-mode", default="sync",
                   choices=["sync", "async"],
                   help="sync = direct Pipeline; async = full actor mesh "
                        "with metrics + cooldown + watchdog loops "
                        "(default: sync)")
    p.add_argument("--review-timeout-s", type=float, default=600.0,
                   metavar="SEC",
                   help="[async] auto-resolve human-review tickets after "
                        "this many seconds (default: 600)")
    p.add_argument("--dynamic-risk-threshold", type=float, default=0.6,
                   metavar="FLOAT",
                   help="[async] minimum risk score required to trigger "
                        "dynamic-rule synthesis (default: 0.6)")
    p.add_argument("--dynamic-cooldown-s", type=float, default=10.0,
                   metavar="SEC",
                   help="[async] per-(agent,tool) cooldown between "
                        "synthesis fires (default: 10)")
    p.add_argument("--audit-flush-interval-s", type=float, default=5.0,
                   metavar="SEC",
                   help="[async] AuditLoop sink-flush interval (default: 5)")

    # File watcher (hot-reload)
    p.add_argument("--watch", action="store_true",
                   help="Enable background file watcher: automatically reload "
                        "rules when .rules files change on disk. "
                        "Watches the paths given by --policy.")
    p.add_argument("--watch-interval", type=float, default=5.0,
                   metavar="SEC",
                   help="[--watch] polling interval in seconds when watchdog "
                        "is not installed (default: 5)")


def _add_check_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "check",
        help="Deep-validate rule file(s) with fix suggestions",
        description=(
            "Parse, compile, and semantically validate AgentGuard rule files. "
            "Reports errors, warnings, and improvement hints with actionable suggestions."
        ),
    )
    p.add_argument("policy", nargs="*", metavar="PATH",
                   help="Rule file(s) or directory (glob patterns supported)")
    p.add_argument("--stdin", action="store_true",
                   help="Read rule source from stdin instead of files")
    p.add_argument("--json", action="store_true",
                   help="Output diagnostics as JSON (useful for editor integrations)")
    p.add_argument("--no-color", action="store_true",
                   help="Disable ANSI colour codes in output")


def _add_validate_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("validate", help="Compile policy file(s) and exit")
    p.add_argument("policy", nargs="+", metavar="PATH",
                   help="Policy file(s) or directories")


def _add_health_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("health", help="GET /health on a running runtime")
    p.add_argument("--url", default="http://localhost:38080",
                   help="Runtime base URL (default: http://localhost:38080)")
    p.add_argument("--api-key", default="",
                   help="X-Api-Key value if required by the server")
    p.add_argument("--timeout", type=float, default=5.0,
                   help="HTTP timeout in seconds (default: 5)")


def _add_metrics_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "metrics",
        help="GET /metrics on a running async runtime",
    )
    p.add_argument("--url", default="http://localhost:38080")
    p.add_argument("--api-key", default="")
    p.add_argument("--timeout", type=float, default=5.0)


def _add_eval_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "eval",
        help="Evaluate a single event JSON against a runtime or local policy",
    )
    p.add_argument("--event", required=True, metavar="JSON_FILE",
                   help="Path to a JSON-encoded RuntimeEvent")
    # Remote evaluation
    p.add_argument("--url", metavar="URL",
                   help="Send to a running runtime (omit to evaluate locally)")
    p.add_argument("--api-key", default="")
    p.add_argument("--timeout", type=float, default=10.0)
    # Local evaluation
    p.add_argument("--policy", action="append", metavar="PATH",
                   help="Local policy file/directory (used when --url omitted)")
    p.add_argument("--no-builtin", action="store_true")
    p.add_argument("--mode", default="enforce",
                   choices=["enforce", "monitor", "dry_run"])


def main(argv: list[str] | None = None) -> int:
    _load_local_env_file()
    parser = argparse.ArgumentParser(
        prog="agentguard",
        description="AgentGuard — runtime access control plane for agent tool-use",
    )
    sub = parser.add_subparsers(dest="command", required=False)

    _add_serve_parser(sub)
    _add_check_parser(sub)
    _add_validate_parser(sub)
    _add_health_parser(sub)
    _add_metrics_parser(sub)
    _add_eval_parser(sub)

    ns = parser.parse_args(argv)

    if ns.command == "serve":
        _cmd_serve(ns)
        return 0
    if ns.command == "check":
        return _cmd_check(ns)
    if ns.command == "validate":
        return _cmd_validate(ns)
    if ns.command == "health":
        return _cmd_health(ns)
    if ns.command == "metrics":
        return _cmd_metrics(ns)
    if ns.command == "eval":
        return _cmd_eval(ns)

    parser.print_help()
    return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(main())
