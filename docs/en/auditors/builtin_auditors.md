# Builtin Auditors

AgentGuard includes built-in backend auditing capabilities for reviewing stored traces and agent behavior after runtime.

## trace_risk_summary

`trace_risk_summary` is a trace auditor. It summarizes decisions and risk signals in one selected `session_id` / `agent_id` / `user_id` trace. Use it for fast investigation of a specific session through `POST /v1/backend/audit/custom/run`.

## rule_agent_security

`rule_agent_security` is the deterministic agent-wide auditor. It reviews the stable snapshot selected for one agent across all included users, sessions, and trace events without calling an external model.

It is suitable for continuous, repeatable, and inexpensive checks. Built-in rules cover sensitive-file access, sensitive data passed to outbound tools, actions that continue after denial, repeated evasion, identity-boundary anomalies, and degraded security controls. Findings cite stored session and event IDs and are semantically deduplicated.

Choose it when you need stable results, do not have an LLM configured, or want to validate rule coverage first.

## llm_agent_security

`llm_agent_security` performs evidence-validated semantic analysis over one agent's stable snapshot. Every session is analyzed in bounded chunks, followed by an aggregate pass for cross-user and cross-session patterns.

The LLM must return structured findings that cite existing session and event IDs. Findings without valid evidence are discarded, suspected findings cannot be critical, and sensitive values are redacted before model calls. This mode fails when no real model is configured; it does not present heuristic output as an LLM conclusion.

Choose it when semantic intent, multi-step behavior, or correlations that are difficult to express as deterministic rules are the primary concern.

## hybrid_agent_security

`hybrid_agent_security` is the default agent-wide auditor. It runs `rule_agent_security` first, passes deterministic evidence into `llm_agent_security`, and merges semantically equivalent findings so the same risk is not reported twice.

If the LLM is unavailable, deterministic findings remain in the report and the limitation is recorded in metadata. A completed LLM pass adds evidence-validated semantic and cross-session analysis but cannot remove rule findings.

Choose it for the broadest review when a model is configured while retaining deterministic coverage and a useful fallback.

All three agent auditors extend `BaseAgentAuditor` and are registered with `@register`. LLM-backed auditors use `from_config()` for request-scoped model settings. See [Agent Security Audit](../security_audit.md) for the administrator UI, API, snapshot coverage, and model configuration.

Use built-in auditors when you want a ready-to-use audit workflow without writing custom backend code.
