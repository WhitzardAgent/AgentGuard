(function () {
  const LANGUAGE_KEY = "agentguard.language";
  const DEFAULT_LANGUAGE = "en";
  const SUPPORTED_LANGUAGES = new Set(["en", "zh"]);
  const OBSERVED_ATTRIBUTES = ["title", "placeholder", "aria-label"];
  const EXACT_TRANSLATIONS = {
    zh: {
      "AgentGuard Frontend Preview": "AgentGuard 前端预览",
      Home: "首页",
      Agents: "智能体",
      Plugins: "插件",
      Labels: "标签",
      Rules: "规则",
      User: "用户",
      Doc: "文档",
      GitHub: "GitHub",
      DashBoard: "仪表盘",
      "Current User": "当前用户",
      "AgentGuard Home": "AgentGuard 首页",
      "keeps your agent workflow in control.": "让你的智能体工作流变得可控。",
      "Start with agent selection, configure plugins when needed, and optionally add rule-based controls for labels, rules, and runtime review.": "选择一个智能体，按需配置访问控制插件，并在仪表盘中查看运行状态。",
      "Start with agent selection, then configure plugins and optional rule-based workflows.": "从选择智能体开始，然后配置插件和可选的规则化工作流。",
      "Choose which registered agent you want to inspect and use that selection to scope the rest of the frontend.": "选择你要查看的智能体，并让该选择作用于整个前端范围。",
      "Enable remote or local plugins for the selected agent, including optional built-in policy and safety flows.": "为所选智能体启用远程或本地插件，包括可选的内置策略与安全流程。",
      "Watch runtime activity for the selected agent. Rule-based plugins can add more policy-specific signals, but dashboard visibility is always available.": "通过仪表盘随时观察所选智能体的运行时活动。",
      "Start WITH Agent Selection": "从智能体选择开始",
      "Choose an agent": "选择一个智能体",
      "to focus the monitoring surface.": "来聚焦监控视图。",
      "Agent Monitoring": "智能体监控",
      "Available Agents": "可用智能体",
      "Preparing agent catalog...": "正在准备智能体目录…",
      "Refresh agent catalog": "刷新智能体目录",
      "Clear selected agent": "清除当前智能体",
      "AgentGuard sections": "AgentGuard 分区",
      "Agent scoped sections": "智能体作用域分区",
      "Current agent context": "当前智能体上下文",
      "Current user context": "当前用户上下文",
      "Choose which registered agent to watch from the agent list.": "从智能体列表中选择你想监控的已注册智能体。",
      "Agent Selection": "智能体选择",
      "Choose which registered agent you want to keep in view across the frontend.": "选择你希望在整个前端中持续关注的已注册智能体。",
      "No agents are discoverable yet. Sync the tool catalog after agents register tools.": "暂时还没有已发现的智能体。请在智能体注册后同步工具目录。",
      "No tools registered.": "尚未注册任何工具。",
      "Refreshing agent catalog...": "正在刷新智能体目录…",
      "Syncing agent catalog...": "正在同步智能体目录…",
      "Synced just now": "刚刚已同步",
      "Agent catalog refreshed.": "智能体目录已刷新。",
      "Showing the built-in empty agent catalog fallback.": "正在显示内置的空智能体目录兜底数据。",
      "Failed to refresh agent catalog.": "刷新智能体目录失败。",
      "Plugin Config": "插件配置",
      "Configure plugins for": "为",
      "the selected agent": "当前选中的智能体",
      "Configure plugins for the selected agent.": "为当前选中的智能体 配置插件。",
      "Configure server plugins and client plugins separately. Changes save immediately and apply to the next guarded events.": "分别配置服务端插件和客户端插件。变更会立即保存，并在下一次受保护事件中生效。",
      "Available Plugins": "可用插件",
      "Loading plugin catalog...": "正在加载插件目录…",
      Server: "服务端",
      Client: "客户端",
      "Loading server plugins...": "正在加载服务端插件…",
      "Loading client plugins...": "正在加载客户端插件…",
      "Refresh plugin catalog": "刷新插件目录",
      "No server plugins are available for this agent yet.": "该智能体暂无可用的服务端插件。",
      "No client plugins are available for this agent yet. Start a client config API to discover client-side plugins.": "该智能体暂无可用的客户端插件。请启动客户端配置 API 以发现客户端插件。",
      "Plugin Config": "插件配置",
      "Configure server and client plugin scopes for the selected agent.": "为选中的智能体配置服务端与客户端插件范围。",
      "Phase not declared": "未声明阶段",
      "No plugin description provided.": "未提供插件描述。",
      "Select an agent first.": "请先选择一个智能体。",
      "Using server default plugin config": "使用服务端默认插件配置",
      "Current plugins": "当前插件",
      "Refreshing plugin catalog...": "正在刷新插件目录…",
      "Loading plugin catalog...": "正在加载插件目录…",
      "Plugin catalog refreshed.": "插件目录已刷新。",
      "Failed to load plugin catalog.": "加载插件目录失败。",
      "Plugin config updated.": "插件配置已更新。",
      "Failed to update plugin config.": "更新插件配置失败。",
      "Labels Studio": "标签工作台",
      "Tool Labels": "工具标签",
      "Review the current tool catalog, inspect default metadata, and adjust label values.": "查看当前工具目录、检查默认元数据，并调整标签值。",
      "Tool Label Editor": "工具标签编辑器",
      "This page loads `/api/tools`, caches the result locally, and lets you refine labels on top of the current tool definitions.": "此页面会加载 `/api/tools`，将结果缓存到本地，并允许你在当前工具定义之上细化标签。",
      "Refresh tool catalog": "刷新工具目录",
      "Preparing tool catalog...": "正在准备工具目录…",
      "Select a tool": "选择一个工具",
      "Selected Label Rows": "已选标签",
      "Labels to write": "待写入标签",
      "Labels to write:": "待写入标签：",
      "Save Tool Labels": "保存工具标签",
      "Reset Selection": "重置选择",
      "Configured Tool Labels": "已配置工具标签",
      "This table shows the catalog values currently available to the labels, rules, and runtime pages.": "此表展示当前可供标签、规则和运行时页面使用的目录值。",
      Agent: "智能体",
      Tool: "工具",
      Boundary: "边界",
      Sensitivity: "敏感度",
      Integrity: "完整性",
      "Inspect the tool catalog, tune label values, and keep the shared label surface clean.": "检查工具目录、调整标签值，并保持共享标签面整洁。",
      "Choose an agent first to load that agent's tools.": "请先选择一个智能体以加载该智能体的工具。",
      "Waiting for agent selection": "等待选择智能体",
      "Select a tool first.": "请先选择一个工具。",
      "No label rows selected yet. Click + to add one.": "还没有选择任何标签行。点击 + 添加一行。",
      "Click + to add a label row.": "点击 + 添加一行标签。",
      "Select a tool, then click + to add a label row.": "先选择一个工具，再点击 + 添加标签行。",
      "Select category": "选择类别",
      "Select value": "选择值",
      "Select category first": "请先选择类别",
      "Choose an agent first.": "请先选择一个智能体。",
      "Tool catalog refreshed.": "工具目录已刷新。",
      "Showing the built-in empty tool catalog fallback.": "正在显示内置的空工具目录兜底数据。",
      "No sync yet": "尚未同步",
      "Failed to refresh tool catalog.": "刷新工具目录失败。",
      "Select a valid tool first.": "请先选择一个有效工具。",
      "Failed to save tool labels.": "保存工具标签失败。",
      "Runtime Monitor": "运行时监控",
      "Runtime Overview": "运行时概览",
      Connecting: "连接中",
      "Refresh Now": "立即刷新",
      "Total Requests": "总请求数",
      Denied: "拒绝数",
      "Pending Approvals": "待审批",
      "Deny Rate": "拒绝率",
      "Agent:": "智能体：",
      "Rule Version:": "规则版本：",
      "Mode:": "模式：",
      "Runtime:": "运行时：",
      "Uptime:": "运行时长：",
      "Recent Traffic": "最近流量",
      "Recent Audit": "最近审计",
      Session: "会话",
      Decision: "决策",
      Risk: "风险",
      "Matched Rules": "命中规则",
      "Selected Audit Detail": "审计详情",
      "Select an audit row to inspect event and decision JSON.": "选择一条审计记录以查看事件和决策 JSON。",
      Healthy: "健康",
      Connected: "已连接",
      Partial: "部分可用",
      Unreachable: "不可达",
      "No target summary available.": "没有可用的目标摘要。",
      "No recent traffic in the current runtime window.": "当前运行时窗口内暂无最近流量。",
      "No pending human-check tickets right now.": "当前没有待处理的人审工单。",
      Approve: "批准",
      Deny: "拒绝",
      "Audit data is unavailable.": "审计数据不可用。",
      "No audit records have been captured yet.": "尚未捕获任何审计记录。",
      "No audit detail available.": "没有可用的审计详情。",
      "Traffic payload has an unexpected format.": "流量数据格式不符合预期。",
      "Approvals payload has an unexpected format.": "审批数据格式不符合预期。",
      "Audit payload has an unexpected format.": "审计数据格式不符合预期。",
      "Failed to resolve approval ticket.": "处理审批工单失败。",
      "Runtime data refreshed.": "运行时数据已刷新。",
      "Failed to refresh runtime data.": "刷新运行时数据失败。",
      "Runtime Overview": "运行时概览",
      "Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for the selected agent.": "查看当前所选智能体的运行时指标、流量、审批与审计活动。",
      "Rule Studio": "规则工作台",
      "Rule Builder": "规则构建器",
      "Build DSL rules from structured inputs and manage rule publication.": "通过结构化输入构建 DSL 规则，并管理规则发布。",
      "Guided Rule Builder": "引导式规则构建器",
      "Create a new rule step by step, or load an existing rule into the editor for modification.": "逐步创建新规则，或将已有规则载入编辑器进行修改。",
      "Back To Guided Create": "返回引导创建",
      "1. Name": "1. 名称",
      "2. Match": "2. 匹配",
      "3. Condition": "3. 条件",
      "4. Details": "4. 详情",
      "Step 1": "步骤 1",
      "Rule Name": "规则名称",
      "Start rule construction with a rule name.": "从规则名称开始构建规则。",
      "Continue To Match Mode": "继续到匹配模式",
      "Step 2": "步骤 2",
      "Formal Match Mode": "形式匹配模式",
      "Choose the ideal path matching approach for your rule.": "为你的规则选择合适的路径匹配方式。",
      "Single Tool": "单工具",
      "I just want constraints on a single tool.": "我只想对单个工具添加约束。",
      "Tool Trace": "工具调用链",
      "I want to monitor an execution chain. In trace mode, any tool / trigger stage filter refers to the last tool on the tool trace.": "我想监控一条执行链。在该模式下，任何工具/触发阶段筛选都指向该工具轨迹中的最后一个工具。",
      "Select Target Tool and Trigger Stage": "选择目标工具与触发阶段",
      "When you use": "当你使用",
      ", the": "时，这里的",
      "here apply to the": "会作用于该轨迹中的",
      "on that trace.": "。",
      "Trigger Stage": "触发阶段",
      "Choose when the rule should trigger in the tool invocation lifecycle.": "选择规则应在工具调用生命周期的哪个阶段触发。",
      "Trigger stage (optional)": "触发阶段（可选）",
      "requested (pre call)": "requested（调用前）",
      "completed (post call)": "completed（调用后）",
      "failed (call failed)": "failed（调用失败）",
      "Optionally narrow the rule to one specific tool under the selected trigger stage.": "可选地将规则收窄到所选触发阶段下的某个具体工具。",
      "Select tool (optional)": "选择工具（可选）",
      "e.g. Tool A -> * -> Tool C": "例如：Tool A -> * -> Tool C",
      "Continue To Condition": "继续到条件配置",
      Back: "返回",
      "Step 3": "步骤 3",
      "Condition Builder": "条件构建器",
      "Craft single conditions and combine them into complex ones.": "创建单个条件，并将它们组合成复杂条件。",
      "Build single conditions with the guided flow first, then assemble them into nested AND / OR logic below.": "先通过引导流程构建单个条件，然后在下方将它们组装成嵌套的 AND / OR 逻辑。",
      "Add condition": "添加条件",
      "Continue To Details": "继续到详情",
      "Step 4": "步骤 4",
      "Additional Details": "附加详情",
      "Finish the optional metadata that helps operators understand and manage the rule later.": "完善可选元数据，帮助后续运维理解和管理该规则。",
      ACTION: "动作",
      "SELECT ACTION": "选择动作",
      Prompt: "提示词",
      "LLM review system prompt for this rule.": "该规则对应的 LLM 审查系统提示词。",
      "DEGRADE Target": "DEGRADE 目标",
      "Select target tool": "选择目标工具",
      Severity: "严重级别",
      "Select severity": "选择严重级别",
      Category: "分类",
      Reason: "原因",
      DESCRIPTION: "描述",
      "Use this field to capture the operator-facing explanation for the rule.": "用这个字段记录面向运维人员的规则说明。",
      "Preview Result": "预览结果",
      "Generate Rule": "生成规则",
      "Check Rule": "校验规则",
      "Clear Form": "清空表单",
      "Rule List": "规则列表",
      All: "全部",
      Published: "已发布",
      Unpublished: "未发布",
      "Build rules from structured inputs, preview DSL output, and manage unpublished and published states.": "从结构化输入构建规则、预览 DSL 输出，并管理未发布与已发布状态。",
      "Local draft": "本地草稿",
      "Built-in": "内置",
      "Default pack": "默认规则包",
      "Agent runtime": "智能体运行时",
      "Rule validation failed.": "规则校验失败。",
      "Rule check passed.": "规则校验通过。",
      "Failed to check rule.": "校验规则失败。",
      "Select an agent before publishing a rule.": "发布规则前请先选择一个智能体。",
      "Failed to build DSL source.": "构建 DSL 源失败。",
      "Failed to publish rules.": "发布规则失败。",
      "Select an agent before deleting a published rule.": "删除已发布规则前请先选择一个智能体。",
      "Failed to restore the disabled published rule back into the local rule list.": "将已停用的发布规则恢复到本地规则列表失败。",
      "Failed to disable rule.": "停用规则失败。",
      "Active rules refreshed.": "活动规则已刷新。",
      "Failed to load active rules.": "加载活动规则失败。",
      "Guided rule builder reset.": "引导式规则构建器已重置。",
      "Select tool": "选择工具",
      "No tools available": "没有可用工具",
      "Please finish the TRACE builder before continuing.": "继续前请先完成 TRACE 构建器。",
      "Please configure the ON filter before continuing.": "继续前请先配置 ON 过滤条件。",
      "Please enter a rule name first.": "请先输入规则名称。",
      "Please select an action first.": "请先选择一个动作。",
      "Please select a DEGRADE target first.": "请先选择一个 DEGRADE 目标。",
      "Rule Editor": "规则编辑器",
      "The legacy full-form editor is kept for modifying existing rules and drafts.": "保留旧版完整表单编辑器，用于修改已有规则和草稿。",
      "Create a new rule step by step.": "按步骤创建新规则。",
      "Edit path": "编辑路径",
      "Add path segment": "添加路径段",
      "PATH must contain at least one concrete segment.": "PATH 至少需要包含一个具体段。",
      "PATH must start with Tool A.": "PATH 必须从 Tool A 开始。",
      "PATH cannot end with a wildcard segment.": "PATH 不能以通配段结束。",
      "PATH is valid.": "PATH 有效。",
      "Build Tool TRACE by adding one or more concrete or wildcard segments. Any tool or trigger stage filter refers to the final tool on the trace.": "通过添加一个或多个具体段或通配段来构建 Tool TRACE。任何工具或触发阶段筛选都指向该轨迹中的最后一个工具。",
      "PATH:": "PATH：",
      "TRACE is empty. Click + to add the first segment.": "TRACE 为空。点击 + 添加第一个段。",
      Start: "开始",
      "Confirm path": "确认路径",
      "Delete path segment": "删除路径段",
      "There are no published runtime rules right now.": "当前没有已发布的运行时规则。",
      "There are no unpublished local rules yet. Generate one to keep it here before publishing.": "当前还没有未发布的本地规则。先生成一条，再在发布前保留在这里。",
      "There are no rules yet. Generate a local rule or publish one to the runtime.": "当前还没有规则。请生成本地规则，或将规则发布到运行时。",
      "Publish rule": "发布规则",
      "Delete unpublished rule": "删除未发布规则",
      "Disable published rule": "停用已发布规则",
      "Checking...": "检查中…",
      "Waiting for first sync": "等待首次同步",
      "Shared frontend shell is ready.": "共享前端外壳已就绪。",
      "Not synced yet": "尚未同步",
      "Request failed.": "请求失败。",
      "Cannot reach the AgentGuard API.": "无法连接到 AgentGuard API。",
      Unavailable: "不可用",
      "Agent catalog payload has an unexpected format.": "智能体目录返回格式不符合预期。",
      "Tool catalog payload has an unexpected format.": "工具目录返回格式不符合预期。",
      "Rule list payload has an unexpected format.": "规则列表返回格式不符合预期。",
      "No rule detail": "无规则详情",
      "No sync yet": "尚未同步",
      "Construct the Trace format you want to monitor.": "构造你希望监控的 Trace 格式。",
      "Rules Trace On Hint": "当你使用 <strong>Tool Trace</strong> 时，这里的 <strong>Tool</strong> 和 <strong>Trigger Stage</strong> 会作用于该轨迹中的<strong>最后一个工具</strong>。",
      "User Studio": "用户工作台",
      "User Centre": "用户中心",
      "Manage your personal information and settings on this page.": "在此页面管理你的个人信息和设置。",
      "User Management": "用户管理",
      "Coming soon.": "敬请期待。",
      "basic user management features to be added...": "基础用户管理功能即将加入……",
      "Manage frontend user identities and related configuration.": "管理前端用户身份及相关配置。",
      "Manage frontend user identities and related configuration.": "管理前端用户身份及相关配置。",
      "Manage your personal information and settings on this page.": "在此页面管理你的个人信息和设置。",
      "Manage frontend user identities and related configuration.": "管理前端用户身份与相关配置。",
      "Configure server plugins and client plugins separately. Changes save immediately and apply to the next guarded events.": "为智能体配置服务端插件与客户端插件。配置变更会被立即保存，并作用于后续事件。",
      "Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for the selected agent.": "查看选中智能体的运行时指标、流量、审批与审计活动。",
      "Manage frontend user identities and related configuration.": "管理前端用户身份和相关配置。",
      "Close condition builder": "关闭条件构建器",
      "Path rule": "路径规则",
      "Single tool rule": "单工具规则",
      "Rule Scope": "规则范围",
      "Path tool": "路径工具",
      "Generate single rule": "生成单个规则",
      "Next builder step": "下一步",
      "Choose a saved condition from the group's + menu first.": "请先从分组的 + 菜单中选择一个已保存条件。",
      "Saved Conditions": "已保存条件",
      "You can build single conditions here with the guided flow.": "你可以在这里通过引导流程构建单个条件。",
      "No saved conditions yet.": "还没有已保存条件。",
      "Edit saved condition": "编辑已保存条件",
      "Delete saved condition": "删除已保存条件",
      "Add node": "添加节点",
      Group: "分组",
      "Delete condition": "删除条件",
      "Logic Root": "逻辑入口",
      "Set root logic to AND": "将根逻辑设为 AND",
      "Set group logic to AND": "将分组逻辑设为 AND",
      "Set root logic to OR": "将根逻辑设为 OR",
      "Set group logic to OR": "将分组逻辑设为 OR",
      "Delete group": "删除分组",
      "Empty group. Insert a saved condition or a nested group.": "空分组。请插入一个已保存条件或嵌套分组。",
      "Logic Canvas": "逻辑画布",
      "You can combine saved single conditions here to package them into a complex rule.": "你可以在这里组合已保存的单个条件，将它们封装成复杂规则。",
      "CONDITION Preview": "条件预览",
      "CONDITION is locked until TRACE or ON is configured.": "在配置 TRACE 或 ON 之前，CONDITION 会被锁定。",
      "Finish the guided single-condition builder, then save it into the library.": "先完成引导式单条件构建，然后将其保存到条件库。",
      "Create a saved single condition first, then insert it into the logic tree.": "请先创建一个已保存的单条件，再将其插入逻辑树。",
      "Use each group's + menu to insert a saved condition or add a nested group.": "使用每个分组的 + 菜单插入已保存条件，或添加嵌套分组。",
      "Complete the single condition builder, then save it to the library.": "完成单条件构建器后，再将其保存到条件库。",
      "Finish the condition fields before saving.": "保存前请先完成条件字段。",
      "Choose rule scope": "选择规则范围",
      "Select the tool format.": "选择工具格式。",
      "Choose tool node": "选择工具节点",
      "Choose the tool node you want to inspect.": "选择你要检查的工具节点。",
      "Choose property": "选择属性",
      "Select the property and subproperty to constrain.": "选择要约束的属性和子属性。",
      Property: "属性",
      "Sub-property": "子属性",
      Comparison: "比较方式",
      "Choose relation and target value": "选择关系与目标值",
      "Set the comparison operator and the target value.": "设置比较运算符和目标值。",
      "Select sub-property": "选择子属性",
      "Select property": "选择属性",
      "Select comparison": "选择比较方式",
      "Target values": "目标值列表",
      "Target list": "目标列表",
      "Target value": "目标值",
      "Select target value": "选择目标值",
      Value: "值",
      "Numeric value": "数值",
      "One tool name per line, or a collection ref like allowlist.tools": "每行一个工具名，或填写类似 allowlist.tools 的集合引用。",
      "One item per line, or a collection ref like allowlist.http": "每行一个条目，或填写类似 allowlist.http 的集合引用。",
      "One item per line, or a collection ref like denylist.roles": "每行一个条目，或填写类似 denylist.roles 的集合引用。",
      "Numeric value": "数值",
      "Create >": "创建 >",
      "At least one condition is required.": "至少需要一个条件。",
      "One condition is incomplete.": "有一个条件尚未完成。",
      "Trace syntax conditions need a tool selection first.": "Trace 语法条件需要先选择工具。",
      "Context conditions need a valid field path.": "Context 条件需要一个有效的字段路径。",
      "CONDITION is valid.": "CONDITION 有效。",
      "Request failed.": "请求失败。",
      "Cannot reach the AgentGuard API.": "无法访问 AgentGuard API。",
      "Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for the selected agent.": "检查所选智能体的运行时指标、流量、审批和审计活动。",
      "Rule ID is required before deleting a published rule.": "删除已发布规则前必须提供规则 ID。",
      "Active rules payload has an unexpected format.": "活动规则数据格式不符合预期。",
      "Unbalanced parentheses.": "括号不平衡。",
      "Malformed condition expression.": "条件表达式格式错误。",
      "Context conditions require a context path before publishing.": "发布前，Context 条件必须提供 context 路径。",
      "Syntax conditions require a syntax field before publishing.": "发布前，Syntax 条件必须提供 syntax 字段。",
      "Condition operator is required before publishing.": "发布前必须提供条件运算符。",
      "Condition value is required before publishing.": "发布前必须提供条件值。",
      "At least one condition is required before publishing.": "发布前至少需要一个条件。",
      "At least one formal match is required before publishing.": "发布前至少需要一个形式匹配条件。",
      "DEGRADE target is required before publishing.": "发布前必须提供 DEGRADE 目标。",
      "At least one local rule is required before publishing.": "发布前至少需要一个本地规则。",
      "Too hard to use? Ask AI for help": "使用有困难？向 AI 寻求帮助",
      "AI Rule Generation": "AI 规则生成",
      "Inspect runtime activity for the selected agent, with live runtime status shown alongside the agent-scoped metrics below.": "查看所选智能体的实时运行状态和运行时活动记录。",
      "Generate And Refine Candidate DSL": "生成并优化候选 DSL",
      "Describe the policy you want in natural language, inspect the candidate, then apply it into the builder when it looks right.": "通过自然语言描述你的需求并在交互中不断优化规则直至满意，然后将其应用到规则构建器中。",
      "Ask for a policy, review the returned rule and rationale, then keep refining in plain language.": "提出你的策略需求，查看返回的规则和理由，然后继续用自然语言进行优化。",
      "LLM settings" : "LLM 设置",
      "Hide LLM settings" : "隐藏 LLM 设置",
      "Reset Session" : "重置会话",
      "Describe the rule you want and send your first message. The model will answer with a candidate DSL and explanation.": "描述你想要的规则并发送你的第一条消息。模型将返回一个候选 DSL 和解释。",
      Skills: "技能",
      "Skill Security": "Skill 安全检测",
      "Inspect skills for": "检查智能体 Skill：",
      "Inspect reported skills and run static detection for the selected agent.": "查看已上报的 Skill，并为当前智能体运行静态检测。",
      "Inspect reported skill resources and run static detection for the selected agent.": "查看已上报的 Skill 资源，并为当前智能体运行静态检测。",
      "Review the skill resources reported by adapters, run static detection, and keep the result attached to the selected agent.": "查看 adapter 上报的 Skill 资源，运行静态检测，并将检测结果绑定到当前智能体。",
      "Skill summary": "Skill 概览",
      "Risk flagged": "风险标记",
      Files: "文件",
      "Registered Skills": "已注册 Skill",
      "Loading skill catalog...": "正在加载 Skill 目录…",
      "Refresh skill catalog": "刷新 Skill 目录",
      "0 selected": "已选择 0 个",
      "Select one or more skills before running detection.": "运行检测前，请先选择一个或多个 Skill。",
      "Use LLM review": "使用 LLM 审核",
      "LLM review": "LLM 审核",
      "LLM parallel": "LLM 并发",
      "LLM review failed": "LLM 审核失败",
      "LLM not configured": "LLM 未配置",
      "LLM review is not configured. Rule-based result is shown.": "LLM 审核未配置。当前显示规则扫描结果。",
      "LLM review completed without a separate reason.": "LLM 审核已完成，但未返回单独原因。",
      "Rule-based result only. LLM review was not requested.": "仅显示规则扫描结果。未请求 LLM 审核。",
      "Select all": "全选",
      Clear: "清空",
      "Clear selection": "清空选择",
      "Detect selected": "检测已选 Skill",
      Detecting: "检测中",
      "Detection Result": "检测结果",
      "Rule-based conclusion": "规则结论",
      "LLM conclusion": "LLM 结论",
      "Run detection to see rule-based conclusions.": "运行检测后查看规则结论。",
      "Run detection with LLM review enabled to see the LLM conclusion.": "启用 LLM 审核并运行检测后查看 LLM 结论。",
      "Configure environment variables to enable LLM review.": "配置环境变量后才能启用 LLM 审核。",
      "Rule-based scan matched {signal}.": "规则扫描命中 {signal}。",
      "Rule-based scan matched {signal} at {location}.": "规则扫描命中 {signal}，位置 {location}。",
      "the selected agent": "当前智能体",
      "Choose an agent first.": "请先选择一个智能体。",
      "Running static detection. This may take a moment for large skills.": "正在运行静态检测。大型 Skill 可能需要一些时间。",
      "Running static detection...": "正在运行静态检测…",
      "Rule-based scan is running.": "规则扫描正在运行。",
      "Rule-based scan is running. Waiting for LLM response.": "规则扫描正在运行，并等待 LLM 响应。",
      "Rule-based scan is running. LLM review will start after it finishes.": "规则扫描正在运行，完成后将开始 LLM 审核。",
      "Waiting for LLM response...": "正在等待 LLM 响应…",
      "Waiting for LLM response. Do not click Detect again.": "正在等待 LLM 响应，请不要重复点击检测。",
      "LLM review was not requested for this run.": "本次运行未请求 LLM 审核。",
      "Rule-based result is ready. Waiting for an LLM review slot.": "规则结果已就绪，正在等待 LLM 审核槽位。",
      "Detection running": "检测运行中",
      "Detection is running. Results will appear here when the server responds.": "检测正在运行。服务端响应后将在这里显示结果。",
      "Waiting for LLM": "等待 LLM",
      Elapsed: "耗时",
      "just now": "刚刚",
      "Loaded skill catalog just now.": "刚刚已加载 Skill 目录。",
      "Not run": "未运行",
      "No detection has run. Select this skill and click Detect selected.": "尚未运行检测。请选择该 Skill 并点击检测已选 Skill。",
      "No high-confidence risk signals were found.": "未发现高置信度风险信号。",
      "Static detection completed.": "静态检测已完成。",
      "rule signal": "规则信号",
      "skill_markdown": "Skill 文档",
      prompt: "提示词",
      script: "脚本",
      manifest: "清单",
      asset: "资源",
      text: "文本",
      file: "文件",
      "No rule-based findings are attached yet.": "暂时没有规则扫描发现。",
      finding: "发现项",
      "LLM review has not run for this skill.": "该 Skill 尚未运行 LLM 审核。",
      "LLM review skipped.": "LLM 审核已跳过。",
      "No LLM reason returned.": "LLM 未返回原因。",
      "No files were reported for this skill.": "该 Skill 没有上报文件。",
      "No root path reported.": "未上报根目录路径。",
      "Root path": "根目录",
      Resource: "资源",
      "No file breakdown.": "暂无文件类型统计。",
      "Skill content": "Skill 内容",
      "Original files collected by the adapter for this skill.": "adapter 为该 Skill 收集到的原始文件。",
      "Download skill": "下载技能",
      "File preview": "文件预览",
      "Select a file to preview its content.": "请选择一个文件查看内容。",
      "Binary files cannot be previewed in the browser.": "二进制文件无法在浏览器中预览。",
      "File content was omitted by the adapter": "文件内容未由 adapter 上报",
      "No previewable content was reported for this file.": "该文件没有可预览内容。",
      "No files are available to download for this skill.": "该技能没有可下载文件。",
      "No previewable files are available to download for this skill.": "该技能没有可导出的可预览文件。",
      "SKILL.md": "SKILL.md",
      "SKILL.md excerpt": "SKILL.md 摘要",
      "File list": "文件列表",
      Path: "路径",
      Type: "类型",
      Size: "大小",
      "No SKILL.md content was reported.": "未上报 SKILL.md 内容。",
      "No SKILL.md content available in the cached summary. Refresh the skill catalog to load full content.": "缓存摘要中没有可用的 SKILL.md 内容。请刷新 Skill 目录以加载完整内容。",
      "Rule-based findings": "规则扫描发现",
      "Run detection to see rule findings.": "运行检测后查看规则扫描发现。",
      "No rule-based signal details were returned.": "规则扫描未返回详细信号。",
      Category: "类别",
      Confidence: "置信度",
      "Top signal": "主要信号",
      "Raw detector output": "原始扫描说明",
      "No description reported.": "未上报描述。",
      "Unnamed skill": "未命名 Skill",
      "not detected": "未检测",
      malicious: "恶意",
      suspicious: "可疑",
      benign: "良性",
      "unknown framework": "未知框架",
      confidence: "置信度",
      missing: "缺失",
      "Hide details": "收起详情",
      "View details": "查看详情",
      "Hide skill content": "收起 Skill 内容",
      "View skill content": "查看 Skill 内容",
      "Choose an agent first to inspect skills.": "请先选择一个智能体来查看 Skill。",
      "No skills have been reported for this agent yet. Run an adapter or the test agent fixture first.": "该智能体暂未上报 Skill。请先运行 adapter 或测试 agent fixture。",
      "Refreshing skill catalog...": "正在刷新 Skill 目录…",
      "Skill catalog refreshed.": "Skill 目录已刷新。",
      "Skill selection cleared.": "已清空 Skill 选择。",
      "Failed to load skill catalog.": "加载 Skill 目录失败。",
      "Select at least one skill first.": "请先至少选择一个 Skill。",
      "Skill detection failed.": "Skill 检测失败。",
      "MCP Services": "MCP 服务",
      "MCP Security": "MCP 安全检测",
      "Inspect MCP services for": "检查智能体 MCP 服务：",
      "Review MCP service metadata and recovered source reported by adapters, run LLM detection, and keep the result attached to the selected agent.": "查看 adapter 上报的 MCP 服务元数据和恢复源码，运行 LLM 检测，并将检测结果绑定到当前智能体。",
      "MCP summary": "MCP 概览",
      "Registered MCP Services": "已注册 MCP 服务",
      "Loading MCP catalog...": "正在加载 MCP 目录...",
      "Refresh MCP catalog": "刷新 MCP 目录",
      "Select one or more MCP services before running detection.": "运行检测前，请先选择一个或多个 MCP 服务。",
      "Detect selected MCPs": "检测已选 MCP",
      "Detecting MCPs": "MCP 检测中",
      "MCP LLM": "MCP LLM",
      "MCP LLM detection is running.": "MCP LLM 检测正在运行。",
      "Waiting for an LLM review slot.": "正在等待 LLM 审核槽位。",
      "Run detection to see the LLM conclusion.": "运行检测后查看 LLM 结论。",
      "LLM detection completed.": "LLM 检测已完成。",
      "No detection has run. Select this MCP service and click Detect selected.": "尚未运行检测。请选择该 MCP 服务并点击检测已选项。",
      "No MCP tools were reported for this service.": "该 MCP 服务没有上报工具。",
      "Unnamed MCP tool": "未命名 MCP 工具",
      "MCP source": "MCP 源码",
      "Original files and configuration collected by the adapter for this MCP service.": "adapter 为该 MCP 服务收集到的原始文件和配置。",
      "Download MCP": "下载 MCP",
      "Remote endpoint": "远程端点",
      "Entry file": "入口文件",
      "No files were reported for this MCP service.": "该 MCP 服务没有上报文件。",
      "No files are available to download for this MCP service.": "该 MCP 服务没有可下载文件。",
      "No previewable files are available to download for this MCP service.": "该 MCP 服务没有可导出的可预览文件。",
      "unknown transport": "未知传输",
      remote: "远程",
      local: "本地",
      "MCP SDK detected": "检测到 MCP SDK",
      "Unnamed MCP service": "未命名 MCP 服务",
      "Hide MCP content": "收起 MCP 内容",
      "View MCP content": "查看 MCP 内容",
      "Choose an agent first to inspect MCP services.": "请先选择一个智能体来查看 MCP 服务。",
      "No MCP services have been reported for this agent yet. Run an adapter or the MCP test agent fixture first.": "该智能体暂未上报 MCP 服务。请先运行 adapter 或 MCP 测试 agent fixture。",
      "Refreshing MCP catalog...": "正在刷新 MCP 目录...",
      "MCP catalog refreshed.": "MCP 目录已刷新。",
      "MCP selection cleared.": "已清空 MCP 选择。",
      "Failed to load MCP catalog.": "加载 MCP 目录失败。",
      "Select at least one MCP service first.": "请先至少选择一个 MCP 服务。",
      "MCP detection failed.": "MCP 检测失败。",
      "Loaded MCP catalog just now.": "刚刚已加载 MCP 目录。",
      },
  };
  const PATTERN_TRANSLATIONS = {
    zh: [
      { re: /^Now watching (.+)\.$/, replace: "正在监控 $1。" },
      { re: /^Synced (\d+) agents\. Last updated: (.+)$/, replace: "已同步 $1 个智能体。最后更新：$2" },
      { re: /^Last synced (.+)$/, replace: "上次同步：$1" },
      { re: /^Showing cached catalog\. Last successful sync: (.+)$/, replace: "正在显示缓存目录。上次成功同步：$1" },
      { re: /^Select an agent to view (server|client) plugins\.$/, fn: (match) => `请先选择一个智能体 以查看${match[1] === "server" ? "服务端" : "客户端"}插件。` },
      { re: /^(\d+) of (\d+) (server|client) plugins enabled\.$/, fn: (match) => `已启用 ${match[1]} / ${match[2]} 个${match[3] === "server" ? "服务端" : "客户端"}插件。` },
      { re: /^Current plugins for (.+): server \[(.*)\], client \[(.*)\]\.$/, replace: "$1 的当前插件：服务端 [$2]，客户端 [$3]。" },
      { re: /^No plugin config has been applied to (.+) yet\.$/, replace: "尚未为 $1 应用任何插件配置。" },
      { re: /^Using server default plugin config for (.+)\.$/, replace: "正在为 $1 使用服务端默认插件配置。" },
      { re: /^Loaded plugin config for (.+)\.$/, replace: "已加载 $1 的插件配置。" },
      { re: /^Updating plugin config for (.+)\.\.\.$/, replace: "正在更新 $1 的插件配置…" },
      { re: /^Select at least one label for (.+)\.$/, replace: "请至少为 $1 选择一个标签。" },
      { re: /^Labels to write for (.+):$/, replace: "$1 待写入的标签：" },
      { re: /^(.+) labels saved\.$/, replace: "$1 的标签已保存。" },
      { re: /^Approved ticket (.+)\.$/, replace: "已批准工单 $1。" },
      { re: /^Denied ticket (.+)\.$/, replace: "已拒绝工单 $1。" },
      { re: /^Inspect agent-scoped runtime metrics, traffic, approvals, and audit activity for (.+)\.$/, replace: "查看 $1 的运行时指标、流量、审批与审计活动。" },
      { re: /^Rule (.+) was loaded at startup and cannot be disabled here\.$/, replace: "规则 $1 在启动时已加载，无法在这里停用。" },
      { re: /^Disabled rule (.+)\.$/, replace: "已停用规则 $1。" },
      { re: /^Rule (.+) was not created in this workspace and cannot be deleted here\.$/, replace: "规则 $1 不是在当前工作区创建的，无法在这里删除。" },
      { re: /^Deleted unpublished rule (.+)\.$/, replace: "已删除未发布规则 $1。" },
      { re: /^(\d+) tool$/, replace: "$1 个工具" },
      { re: /^(\d+) tools$/, replace: "$1 个工具" },
      { re: /^(\d+) selected$/, replace: "已选择 $1 个" },
      { re: /^(\d+) file$/, replace: "$1 个文件" },
      { re: /^(\d+) files$/, replace: "$1 个文件" },
      { re: /^(\d+) rule finding$/, replace: "$1 个规则发现" },
      { re: /^(\d+) rule findings$/, replace: "$1 个规则发现" },
      { re: /^(\d+) rule signal$/, replace: "$1 个规则信号" },
      { re: /^(\d+) rule signals$/, replace: "$1 个规则信号" },
      { re: /^(\d+) affected file$/, replace: "$1 个受影响文件" },
      { re: /^(\d+) affected files$/, replace: "$1 个受影响文件" },
      { re: /^(\d+) match$/, replace: "$1 次命中" },
      { re: /^(\d+) matches$/, replace: "$1 次命中" },
      { re: /^Select skill (.+)$/, replace: "选择 Skill $1" },
      { re: /^Loading skills for (.+)\.\.\.$/, replace: "正在加载 $1 的 Skill…" },
      { re: /^Loaded (\d+) skills for (.+)\. Last updated: (.+)$/, replace: "已加载 $1 个 $2 的 Skill。最后更新：$3" },
      { re: /^Showing cached skill catalog\. Last successful sync: (.+)$/, replace: "正在显示缓存的 Skill 目录。上次成功同步：$1" },
      { re: /^Detected (\d+) skill\.$/, replace: "已检测 $1 个 Skill。" },
      { re: /^Detected (\d+) skills\.$/, replace: "已检测 $1 个 Skill。" },
      { re: /^(\d+) requested skill\(s\) were missing\.$/, replace: "$1 个请求的 Skill 不存在。" },
      { re: /^(\d+) MCP service$/, replace: "$1 个 MCP 服务" },
      { re: /^(\d+) MCP services$/, replace: "$1 个 MCP 服务" },
      { re: /^Select MCP (.+)$/, replace: "选择 MCP $1" },
      { re: /^Loading MCP services for (.+)\.\.\.$/, replace: "正在加载 $1 的 MCP 服务..." },
      { re: /^Loaded (\d+) MCP services for (.+)\. Last updated: (.+)$/, replace: "已加载 $1 个 $2 的 MCP 服务。最后更新：$3" },
      { re: /^Showing cached MCP catalog\. Last successful sync: (.+)$/, replace: "正在显示缓存的 MCP 目录。上次成功同步：$1" },
      { re: /^Detected (\d+) MCP service\.$/, replace: "已检测 $1 个 MCP 服务。" },
      { re: /^Detected (\d+) MCP services\.$/, replace: "已检测 $1 个 MCP 服务。" },
      { re: /^Toggle plugin (.+)$/, replace: "切换插件 $1" },
      { re: /^session=(.+) \| risk=(.+) \| matched=(.+)$/, replace: "会话=$1 | 风险=$2 | 命中=$3" },
      { re: /^session=(.+) \| risk=(.+) \| reason=(.+)$/, replace: "会话=$1 | 风险=$2 | 原因=$3" },
      { re: /^agent=(.+) \| session=(.+) \| created=(.+)$/, replace: "智能体=$1 | 会话=$2 | 创建时间=$3" },
      { re: /^Rule name "(.+)" is not a valid DSL identifier\.$/, replace: "规则名“$1”不是有效的 DSL 标识符。" },
      { re: /^Action "(.+)" is not supported by the AgentGuard DSL\.$/, replace: "动作“$1”不受 AgentGuard DSL 支持。" },
      { re: /^Unsupported context path "(.+)"\.$/, replace: "不支持的 context 路径“$1”。" },
      { re: /^Unsupported condition feature "(.+)"\.$/, replace: "不支持的条件特性“$1”。" },
      { re: /^ON clause "(.+)" is not a supported tool_call expression\.$/, replace: "ON 子句“$1”不是受支持的 tool_call 表达式。" },
      { re: /^Step (\d+)$/, replace: "步骤 $1" },
      { re: /^param-(.+)$/, replace: "参数-$1" },
    ],
  };

  function normalizeLanguage(language) {
    const normalized = String(language || "").trim().toLowerCase();
    return SUPPORTED_LANGUAGES.has(normalized) ? normalized : DEFAULT_LANGUAGE;
  }

  function readStoredLanguage() {
    try {
      return normalizeLanguage(window.localStorage?.getItem(LANGUAGE_KEY));
    } catch {
      return DEFAULT_LANGUAGE;
    }
  }

  function currentLanguage() {
    return readStoredLanguage();
  }

  function currentLocale() {
    return currentLanguage() === "zh" ? "zh-CN" : "en-US";
  }

  function persistLanguage(language) {
    try {
      window.localStorage?.setItem(LANGUAGE_KEY, normalizeLanguage(language));
    } catch {
      // Ignore localStorage write errors in preview mode.
    }
  }

  function normalizeWhitespace(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function interpolate(template, variables = {}) {
    return String(template || "").replace(/\{(\w+)\}/g, (match, key) => {
      if (!Object.prototype.hasOwnProperty.call(variables, key)) {
        return match;
      }
      return String(variables[key] ?? "");
    });
  }

  function translateValue(value, variables = {}) {
    const language = currentLanguage();
    const normalized = normalizeWhitespace(value);
    if (!normalized || language === "en") {
      return interpolate(value, variables);
    }

    const exactDictionary = EXACT_TRANSLATIONS[language] || {};
    if (Object.prototype.hasOwnProperty.call(exactDictionary, normalized)) {
      return interpolate(exactDictionary[normalized], variables);
    }

    const patterns = PATTERN_TRANSLATIONS[language] || [];
    for (const pattern of patterns) {
      const matched = normalized.match(pattern.re);
      if (!matched) {
        continue;
      }
      if (typeof pattern.fn === "function") {
        return pattern.fn(matched, variables);
      }
      if (pattern.replace) {
        return normalized.replace(pattern.re, pattern.replace);
      }
    }

    return interpolate(value, variables);
  }

  function isExcludedElement(element) {
    if (!(element instanceof HTMLElement)) {
      return false;
    }
    return ["SCRIPT", "STYLE", "NOSCRIPT", "PRE"].includes(element.tagName);
  }

  function translateTextNode(node) {
    const original = String(node.nodeValue || "");
    const normalized = normalizeWhitespace(original);
    if (!normalized) {
      return;
    }
    const translated = translateValue(normalized);
    if (translated === normalized) {
      return;
    }
    const leading = original.match(/^\s*/)?.[0] || "";
    const trailing = original.match(/\s*$/)?.[0] || "";
    node.nodeValue = `${leading}${translated}${trailing}`;
  }

  function translateAttribute(element, attributeName) {
    if (!(element instanceof HTMLElement) || !element.hasAttribute(attributeName)) {
      return;
    }
    const original = element.getAttribute(attributeName);
    const normalized = normalizeWhitespace(original);
    if (!normalized) {
      return;
    }
    const translated = translateValue(normalized);
    if (translated !== normalized) {
      element.setAttribute(attributeName, translated);
    }
  }

  function translateExplicitBindings(element) {
    if (!(element instanceof HTMLElement)) {
      return;
    }

    const textKey = element.getAttribute("data-i18n");
    if (textKey) {
      element.textContent = translateValue(textKey);
    }

    const htmlKey = element.getAttribute("data-i18n-html");
    if (htmlKey) {
      element.innerHTML = translateValue(htmlKey);
    }

    const placeholderKey = element.getAttribute("data-i18n-placeholder");
    if (placeholderKey) {
      element.setAttribute("placeholder", translateValue(placeholderKey));
    }

    const titleKey = element.getAttribute("data-i18n-title");
    if (titleKey) {
      element.setAttribute("title", translateValue(titleKey));
    }

    const ariaLabelKey = element.getAttribute("data-i18n-aria-label");
    if (ariaLabelKey) {
      element.setAttribute("aria-label", translateValue(ariaLabelKey));
    }

    const valueKey = element.getAttribute("data-i18n-value");
    if (valueKey && (element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement)) {
      element.value = translateValue(valueKey);
    }
  }

  function applyToElement(element) {
    if (!(element instanceof HTMLElement) || isExcludedElement(element)) {
      return;
    }
    const elements = [element];
    if (typeof element.querySelectorAll === "function") {
      elements.push(...element.querySelectorAll("*"));
    }
    elements.forEach((item) => {
      if (!(item instanceof HTMLElement) || isExcludedElement(item)) {
        return;
      }
      translateExplicitBindings(item);
      OBSERVED_ATTRIBUTES.forEach((attributeName) => translateAttribute(item, attributeName));
    });
    const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
    const textNodes = [];
    let currentNode = walker.nextNode();
    while (currentNode) {
      if (currentNode.parentElement && !isExcludedElement(currentNode.parentElement)) {
        textNodes.push(currentNode);
      }
      currentNode = walker.nextNode();
    }
    textNodes.forEach(translateTextNode);
  }

  function applyToNode(node) {
    if (!node) {
      return;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      if (node.parentElement && !isExcludedElement(node.parentElement)) {
        translateTextNode(node);
      }
      return;
    }
    if (node.nodeType === Node.ELEMENT_NODE) {
      applyToElement(node);
    }
  }

  function applyDocumentTranslations() {
    if (typeof document === "undefined") {
      return;
    }
    if (document.documentElement) {
      document.documentElement.lang = currentLanguage() === "zh" ? "zh-CN" : "en";
    }
    if (document.title) {
      document.title = translateValue(document.title);
    }
    if (document.body) {
      applyToElement(document.body);
    }
    renderLanguageToggle();
  }

  function renderLanguageToggle() {
    const button = document.getElementById("sidebar-language-toggle") || document.getElementById("locale-toggle-button");
    if (!(button instanceof HTMLButtonElement)) {
      return;
    }
    const language = currentLanguage();
    button.textContent = language === "zh" ? "English" : "中文";
    button.setAttribute("aria-label", language === "zh" ? "Switch to English" : "Switch to Chinese");
    button.setAttribute("title", language === "zh" ? "Switch to English" : "Switch to Chinese");
    if (button.dataset.boundLanguageToggle === "true") {
      return;
    }
    button.addEventListener("click", () => {
      const nextLanguage = currentLanguage() === "zh" ? "en" : "zh";
      setLanguage(nextLanguage);
    });
    button.dataset.boundLanguageToggle = "true";
  }

  function observeMutations() {
    if (typeof MutationObserver === "undefined" || currentLanguage() !== "zh" || !document.body) {
      return;
    }
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((mutation) => {
        if (mutation.type === "characterData") {
          applyToNode(mutation.target);
          return;
        }
        if (mutation.type === "attributes") {
          applyToNode(mutation.target);
          return;
        }
        mutation.addedNodes.forEach(applyToNode);
      });
    });
    observer.observe(document.body, {
      subtree: true,
      childList: true,
      characterData: true,
      attributes: true,
      attributeFilter: OBSERVED_ATTRIBUTES,
    });
  }

  function setLanguage(language, options = {}) {
    const normalized = normalizeLanguage(language);
    persistLanguage(normalized);
    if (options.reload === false) {
      applyDocumentTranslations();
      return normalized;
    }
    if (typeof window !== "undefined" && window.location) {
      window.location.reload();
    }
    return normalized;
  }

  if (currentLanguage() !== "zh") {
    persistLanguage(DEFAULT_LANGUAGE);
  }

  window.AgentGuardI18n = {
    getLanguage: currentLanguage,
    getLocale: currentLocale,
    setLanguage,
    apply: applyDocumentTranslations,
    t: translateValue,
  };

  function initialize() {
    applyDocumentTranslations();
    observeMutations();
  }

  if (typeof document !== "undefined" && document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initialize, { once: true });
  } else {
    initialize();
  }
})();
