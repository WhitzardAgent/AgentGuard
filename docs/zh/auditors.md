# AgentGuard 审计器

AgentGuard 支持在后端对已存储的运行时轨迹执行事后审计。和在实时链路中同步执行的 plugin 不同，auditor 会在事件落库后，对已有 trace 和安全证据做回溯分析。

如果你想理解或扩展 AgentGuard 的审计层，可以从本章节开始：

- [内置审计器](auditors/builtin_auditors.md)：AgentGuard 自带的审计能力。
- [自定义审计器](auditors/custom_auditors.md)：如何基于已存储 trace 实现你自己的 auditor。

在实际使用中，plugin 和 auditor 是互补关系：

- plugin 在智能体执行过程中做实时的 allow、deny 或 review 决策；
- auditor 在事后复核已存储结果，用于合规、调查和回溯风险分析。
