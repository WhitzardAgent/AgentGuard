# AgentGuard Auditor

AgentGuard supports backend auditors for post-hoc review over stored runtime traces. Unlike plugins, which run inline during the live runtime, auditors analyze already-recorded traces and security evidence after the fact.

Use this section when you want to understand or extend AgentGuard's auditing layer:

- [Builtin Auditors](auditors/builtin_auditors.md): built-in auditor capabilities shipped with AgentGuard.
- [Custom Auditors](auditors/custom_auditors.md): how to implement your own auditors against stored traces.

In practice, plugins and auditors complement each other:

- plugins make inline allow, deny, or review decisions during agent execution;
- auditors review the stored results afterward for compliance, investigation, and retrospective risk analysis.
