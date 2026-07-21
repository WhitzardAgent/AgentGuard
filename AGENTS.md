# AGENTS.md

Guidance for AI coding agents (and human contributors) working in this repository.
See [CONTRIBUTING.md](./CONTRIBUTING.md) for the human-oriented contribution guide,
and [README.md](./README.md) / [README_CN.md](./README_CN.md) for a product overview.

## Project Summary

AgentGuard is a zero-trust security foundation for AI agents. It intercepts
LLM/tool calls before and after execution and evaluates them against
configurable, pluggable access-control policies. It ships two runtimes:

- A **client** SDK (Python and JS) that attaches to agent frameworks
  (LangChain, LangGraph, AutoGen, LlamaIndex, OpenAI Agents SDK, Dify,
  MetaGPT, OpenClaw, ...) and intercepts LLM/tool calls.
- A **control server** (FastAPI backend + a small static-JS frontend) that
  evaluates policies, stores runtime traces, and exposes a web console.

Because this is a security-oriented project, correctness of the policy
engine, rule matching, and decision aggregation is safety-critical — treat
changes in `src/shared/rules/`, `src/server/backend/runtime/`, and
`src/client/*/agentguard/u_guard/` with extra care and test coverage.

## Repository Layout

```
src/client/python/agentguard/   Python client SDK (adapters, guard, sandbox, rules)
src/client/js/agentguard/       JS client SDK (mirrors the Python SDK; Node test runner)
src/server/backend/             Control server: runtime engine, plugins, API, audit
src/server/frontend/            Static-JS + Jinja web console served by the backend
src/shared/                     Schemas, rule DSL, protocol shared by client and server
skills/                         Skill manifests + built-in developer/runtime skills
docs/                           GitBook docs, bilingual: docs/en/**, docs/zh/**
rules/                          Example .rules policy files used by docs/tests
config/                         Example plugin/tool configuration JSON
scripts/                        Dev/e2e/deployment shell scripts
tests/                          Python test suite (pytest)
```

Root `README.md` (English) / `README_CN.md` (简体中文) follow the same
bilingual pairing convention as `docs/en/**` / `docs/zh/**`. When you add or
significantly change user-facing docs, update both languages, or clearly flag
in the PR that a translation follow-up is needed.

## Setup

Python (>= 3.11 required):

```bash
python -m venv .venv && source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -e ".[dev,server]"
```

JS client (Node.js >= 18, uses the built-in `node:test` runner — no test
framework dependency):

```bash
npm install
```

## Build / Test / Lint Commands

Run these before opening a PR; CI (`.github/workflows/ci.yml`) runs the same
commands.

| Purpose               | Command |
|------------------------|---------|
| Python lint            | `ruff check src tests` |
| Python type check      | `mypy src` (advisory in CI — see note below) |
| Python tests           | `pytest -q` |
| One Python test file   | `pytest -q tests/test_parser.py` |
| JS tests (all)         | `npm install && node --test` (run from repo root so tests are auto-discovered) |
| One JS test file       | `node --test src/client/js/agentguard/guard.test.js` |
| End-to-end smoke test  | `./scripts/e2e.sh` (Docker if available, otherwise in-process) |

Notes:

- `ruff` is configured in `pyproject.toml` (`[tool.ruff]`) and is expected to
  pass cleanly — keep it green.
- `mypy` runs in `strict` mode per `pyproject.toml`, but the codebase is not
  yet fully strict-typed (hundreds of pre-existing findings tracked as tech
  debt). CI runs it as an **advisory, non-blocking** job. Don't let mypy
  advisory noise stop you, but please don't add *new* obviously-typed
  violations in files you touch.
- The Python test suite currently has a handful of known, pre-existing
  failures unrelated to typical unrelated changes (see open issues / CI
  history for the current list). If a test you didn't touch is failing
  before your change too, don't feel obligated to fix it as part of an
  unrelated PR — mention it in the PR description instead.

## Coding Conventions

- Python: `from __future__ import annotations`, type hints on new code,
  4-space indent, `ruff` import ordering (`I` rules). Line length is not
  strictly enforced (`E501` is ignored) but keep lines readable.
- JS: plain ES modules/CommonJS matching the surrounding file, tests colocated
  as `*.test.js` / `*.test.cjs` next to the module under test using
  `node:test` + `node:assert`.
- Prefer adding/adjusting tests alongside behavior changes, especially for
  anything under `src/shared/rules/`, `src/server/backend/runtime/`, and the
  client `u_guard`/enforcer modules.
- License: this project is GPLv3 (`./LICENSE`). Don't introduce dependencies
  with incompatible licenses.
- Do not commit secrets, API keys, or real customer data — see
  [SECURITY.md](./SECURITY.md).

## PR Expectations

- Keep PRs focused; unrelated formatting-only churn makes review harder.
- Update `docs/en/**` (and `docs/zh/**` where practical) for user-facing
  behavior changes.
- Link the PR to an issue when one exists, and describe what you validated
  (commands run, tests added).
