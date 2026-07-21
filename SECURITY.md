# Security Policy

AgentGuard is a security tool, so we take vulnerabilities in it — and in the
guarantees it makes to the agents it protects — seriously. Thank you for
helping keep AgentGuard and its users safe.

## Supported Versions

| Version        | Supported          |
| -------------- | ------------------ |
| `main` branch  | :white_check_mark: |
| Latest release (v2.1.x) | :white_check_mark: |
| Older releases | Best effort only    |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub
issues, discussions, or pull requests.**

Instead, please use one of the following private channels:

1. **Preferred:** Open a
   [private security advisory](https://github.com/WhitzardAgent/AgentGuard/security/advisories/new)
   via GitHub's "Report a vulnerability" feature on this repository. This
   keeps the report confidential between you and the maintainers until a fix
   is available.
2. **Alternative:** Email **contact@whitzard.tech** with a description of
   the issue. If you can, encrypt sensitive details or ask for a secure
   channel in your first message.

Please include as much of the following as you can:

- A description of the vulnerability and its potential impact (e.g. policy
  bypass, sandbox escape, information disclosure, denial of service).
- Steps to reproduce, a minimal proof-of-concept, or a relevant policy/rule
  file and agent trace if the issue is in the rule engine.
- The AgentGuard version/commit, deployment mode (client-only, client+server,
  Docker), and any relevant configuration (`config/plugins.json`, `.rules`
  files) with secrets redacted.
- Whether the issue affects the **policy decision path** (i.e. an agent
  action that should have been denied was allowed) — please call this out
  explicitly, since it's the highest-severity class of bug for this project.

## What to Expect

- **Acknowledgement:** within 3 business days.
- **Initial assessment / severity triage:** within 7 business days.
- **Fix or mitigation timeline:** communicated once triage is complete;
  critical policy-bypass issues are prioritized above all other work.
- We will coordinate an embargo/disclosure timeline with you and credit
  reporters (unless you prefer to remain anonymous) in the release notes once
  a fix ships.

## Scope

In scope:

- The client SDKs (`src/client/python/agentguard/**`,
  `src/client/js/agentguard/**`), including interceptors, parsers, and
  sandbox execution.
- The control server (`src/server/backend/**`), including the runtime
  policy engine, rule DSL parser/matcher, and API surface.
- The web console (`src/server/frontend/**`).
- Built-in skills (`skills/**`).

Out of scope (but still welcome as regular issues):

- Vulnerabilities in third-party dependencies without a demonstrated,
  AgentGuard-specific exploitation path (please report upstream first).
- Vulnerabilities requiring an already-fully-compromised host or an attacker
  who already has the privileges the policy is meant to restrict.
- The example `.rules` files under `rules/` and configuration under
  `config/`, which are intentionally illustrative and not meant for
  production use as-is.
