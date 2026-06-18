# 目录

* [快速部署](README.md)
* [概览](overview.md)
* [核心概念](concepts.md)
* 运行时链路
  * [会话生命周期与存储](runtime/session_lifecycle.md)
* 如何在智能体中导入访问控制客户端
  * [LangChain](how-to-plugin/langchain.md)
  * [AutoGen](how-to-plugin/autogen.md)
  * [OpenAI Agents SDK](how-to-plugin/openai_agents_sdk.md)
* [AgentGuard插件](plugins.md)
  * [内置插件](plugins/builtin_plugins.md)
    * [rule_based_plugin](plugins/rule_based_plugin.md)
      * [可视化策略配置](policies/quick_config.md)
      * [策略 DSL 基本结构](policies/dsl_basic_structure.md)
  * [自定义客户端插件](plugins/custom_client_plugin.md)
  * [自定义服务端插件](plugins/custom_server_plugin.md)
* [自定义审计器](auditors.md)
