# DSLWriter Skill

Convert a natural-language policy intent into an AgentGuard rule.

Deterministic templates run first. The skill maps capability keywords
(external_send, shell, file write, network, database, payment, memory write)
and risk keywords (api key, secret, pii, system prompt, prompt injection)
to a rule with an effect (deny, require_approval, require_remote_review,
degrade, sanitize, log_only).

Output is a JSON object: `{"rules": [ <PolicyRule>, ... ]}`.
