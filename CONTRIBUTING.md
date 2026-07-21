# Contributing to AgentGuard

Thanks for your interest in contributing to AgentGuard! This document covers
how to set up your environment, the coding/testing conventions we use, and
how to submit changes.

> Looking for AI-agent-friendly build/test instructions? See
> [AGENTS.md](./AGENTS.md). 简体中文贡献指南见 [CONTRIBUTING_CN.md](./CONTRIBUTING_CN.md)。

Everyone participating in this project is expected to follow our
[Code of Conduct](./CODE_OF_CONDUCT.md). If you discover a security
vulnerability, please follow [SECURITY.md](./SECURITY.md) instead of opening
a public issue.

## Ways to Contribute

- **Report bugs** via [GitHub Issues](https://github.com/WhitzardAgent/AgentGuard/issues/new/choose).
- **Propose features** or discuss design changes via a feature-request issue
  before sending a large PR, so we can align on direction first.
- **Improve docs** under `docs/en/**` (English) and `docs/zh/**` (简体中文).
- **Add framework adapters** for agent frameworks not yet supported (see
  `src/client/python/agentguard/adapters/agent/` and
  `src/client/js/agentguard/adapters/agent/`).
- **Add or improve tests**, especially around the policy/rule engine.

## Development Setup

### Prerequisites

- Python 3.11+
- Node.js 18+ (JS client + docs tooling)
- Docker + Docker Compose (optional, needed for the full end-to-end test and
  for running the control server as described in the README)

### Clone and install

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard

# Python client + server (editable install with dev/server extras)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,server]"

# JS client
npm install
```

Other optional extras you may need depending on what you're working on:
`redis`, `postgres`, `neo4j`, `dynamic` (LiteLLM), `dify`, `sandbox`. See
`pyproject.toml` → `[project.optional-dependencies]`.

## Running Tests

```bash
# Python test suite
pytest -q

# A single test file / test
pytest -q tests/test_parser.py
pytest -q tests/test_parser.py::test_specific_case

# JS test suite (Node's built-in test runner, no extra framework needed)
node --test

# End-to-end smoke test (Docker if available, otherwise in-process HTTP)
./scripts/e2e.sh
```

## Linting & Type Checking

```bash
ruff check src tests     # must pass cleanly
mypy src                 # advisory only for now — see AGENTS.md for context
```

Please run `ruff check src tests` before submitting a PR; CI enforces it. We
run `mypy` in advisory (non-blocking) mode while the codebase incrementally
adopts full strict typing — you don't need to resolve every existing finding,
but please don't introduce obviously-typed regressions in files you touch.

## Pull Request Guidelines

1. **Fork** the repository and create a topic branch off `main`
   (`git checkout -b feat/short-description`).
2. Keep PRs **focused and small** where possible — one logical change per PR
   makes review much faster.
3. Add or update **tests** for behavior changes, especially anything touching
   the rule/policy engine (`src/shared/rules/`,
   `src/server/backend/runtime/`) or the client enforcement path
   (`.../u_guard/`).
4. Update **documentation** (`docs/en/**`, and `docs/zh/**` where practical,
   plus `README.md` / `README_CN.md` for user-facing changes).
5. Make sure `ruff check`, `pytest`, and `node --test` pass locally.
6. Use a clear commit message / PR title, e.g. `feat(langgraph): support X`,
   `fix(rules): correct trace matching for Y`, `docs: clarify Z`.
7. Reference related issues (`Closes #123`) where applicable.
8. A maintainer will review your PR, may request changes, and will merge once
   CI is green and the review is approved.

## Adding a New Framework Adapter

If you're adding support for a new agent framework, mirror the structure of
an existing adapter (e.g. `src/client/python/agentguard/adapters/agent/langgraph.py`)
and add:

- A client adapter implementing the standard attach/patch hooks.
- Unit tests under `tests/` (Python) or alongside the module (JS).
- A short how-to doc under `docs/en/how-to-plugin/` (and ideally
  `docs/zh/how-to-plugin/`).

See the [client plugin guide](https://whitzard.tech/AgentGuard/en/plugins/custom_client_plugin.html)
and [server plugin guide](https://whitzard.tech/AgentGuard/en/plugins/custom_server_plugin.html)
for the extensibility model.

## License

By contributing, you agree that your contributions will be licensed under
the project's [GNU GPLv3 license](./LICENSE).
