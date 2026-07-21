# Agent Security Audit

The security audit module lets administrators review every user, session, and trace produced by one agent in an optional time range. Non-admin users cannot create audits or read audit reports.

## Audit modes

- `rule_agent_security`: deterministic rules only; stable and inexpensive for continuous use.
- `llm_agent_security`: semantic and cross-session analysis with a real LLM. The run fails when no real model is configured instead of presenting heuristic output as an LLM conclusion.
- `hybrid_agent_security`: the default. Rules run first and an LLM adds semantic analysis. If the model is unavailable, rule findings remain available and the fallback reason is recorded in report metadata.

Auditors extend `BaseAgentAuditor`; model-based auditors extend `BaseLLMAgentAuditor`. Rules extend `BaseAuditRule` and are registered in the built-in rule list. Every model finding must cite an existing session ID and event ID or it is discarded.

## Input, full coverage, and output

An administrator first selects one agent. Without a time range, the input contains every user, session, and trace in that agent's stable snapshot at run creation. The default `AGENTGUARD_AUDIT_MAX_EVENTS=0` applies no event cap. A positive operator cap fails an oversized run instead of returning a partial report.

Rules receive the complete `SessionTrace` list. LLM analysis is hierarchical: every trace enters its session chunk analysis, and the final pass receives the coverage manifest, deterministic findings, and all session assessments for cross-user and cross-session review. This provides complete coverage without assuming arbitrary raw JSON fits in one model context.

The output includes the snapshot event ID, user/session/trace counts, risk level, finding source, `confirmed` or `suspected` verification, real session/event evidence, and mitigation. Rules prioritize sensitive file access, credential or sensitive content sent through outbound tools, execution after denial, repeated evasion, identity boundaries, and degraded security controls. Thought-Aligner is only an optional control signal; the audit subsystem does not depend on it.

## Usage

1. Sign in to the console with an administrator account.
2. Select an agent on the Agents page, then open `/security-audit` from that agent's navigation.
3. To override server model defaults, open **LLM Settings** in the upper-right corner and enter the model, base URL, API key, timeout, and chunk size. These values stay in the current browser, travel only with LLM or hybrid audit requests, and are not stored in the audit database.
4. Select an auditor and optional time range (the page uses Beijing time).
5. Create the run and wait for its status to move from `queued` or `running` to `completed` or `failed`.
6. Review the overall risk, rule/model findings, and their trace evidence.

When the browser settings are blank, the default hybrid mode accepts these model-specific variables and otherwise falls back to AgentGuard's global LLM configuration:

```bash
AGENTGUARD_AUDIT_LLM_BASE_URL=https://example.com/v1
AGENTGUARD_AUDIT_LLM_MODEL=model-name
AGENTGUARD_AUDIT_LLM_API_KEY=secret
AGENTGUARD_AUDIT_LLM_TIMEOUT_S=60
```

`AGENTGUARD_AUDIT_MAX_EVENTS=0` means the full snapshot; set a positive value only as a per-run safety cap. Use `AGENTGUARD_AUDIT_LLM_CHUNK_EVENTS` to control events per model request.

## Administrator API

- `GET /v1/backend/security-audits/auditors`
- `POST /v1/backend/security-audits`
- `GET /v1/backend/security-audits`
- `GET /v1/backend/security-audits/{run_id}`
- `GET /v1/backend/security-audits/{run_id}/findings`
- `GET /v1/backend/security-audits/{run_id}/sessions`

Each run records the highest trace event ID at creation time and reads only that stable snapshot, so new traces created during the audit do not change its result.

## Safe test fixture

`examples/test_security_audit_case.py` is a LangChain agent that never reads real files and never makes a real network request. The `safe` scenario reads a simulated public file. The `exfil` scenario reads a simulated `.env` and passes the same synthetic credentials to a simulated webhook.

```bash
python3 examples/test_security_audit_case.py \
  --ticket 'one-time ticket from the User Centre' \
  --scenario both
```

Select the newly created LangChain agent and run `rule_agent_security` first. The safe scenario should not produce a sensitive-data finding. The exfil scenario should show only the confirmed critical sensitive-data outbound flow (`AUDIT-DATA-002`); the precursor `AUDIT-DATA-001` is covered by the same evidence chain and suppressed as a duplicate.
