"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { GuardDecision, DecisionType } = require("../../../schemas/decisions");
const { UGuardEnforcer } = require("../../../u_guard/enforcer");
const { RemoteGuardClient } = require("../../../u_guard/remote_client");
const { _private } = require("./index");

test("extractProviderBuiltInTools only returns provider-side tools", () => {
  assert.deepEqual(
    _private.extractProviderBuiltInTools([
      { type: "web_search" },
      { type: "file_search" },
      { type: "function", function: { name: "local_tool" } },
      { type: "code_interpreter" },
    ]),
    ["web_search", "file_search", "code_interpreter"]
  );
});

test("hasNonMainConnection detects AI subnode connections", () => {
  assert.equal(_private.hasNonMainConnection(["main"]), false);
  assert.equal(_private.hasNonMainConnection([{ type: "main" }]), false);
  assert.equal(_private.hasNonMainConnection([{ type: "ai_tool" }]), true);
  assert.equal(_private.hasNonMainConnection(["ai_languageModel"]), true);
});

test("shouldTreatRunNodeAsTool skips AI and logic nodes", () => {
  assert.equal(
    _private.shouldTreatRunNodeAsTool(
      { type: "@n8n/n8n-nodes-langchain.agent", name: "AI Agent" },
      { description: { group: ["transform"], inputs: ["main"], outputs: ["main"] } },
      {}
    ),
    false
  );
  assert.equal(
    _private.shouldTreatRunNodeAsTool(
      { type: "n8n-nodes-base.if", name: "If" },
      { description: { group: ["transform"], inputs: ["main"], outputs: ["main"] } },
      {}
    ),
    false
  );
  assert.equal(
    _private.shouldTreatRunNodeAsTool(
      { type: "@n8n/n8n-nodes-langchain.openAi", name: "Message a model" },
      { description: { group: ["transform"], inputs: ["main"], outputs: ["main"] } },
      {}
    ),
    false
  );
  assert.equal(
    _private.shouldTreatRunNodeAsTool(
      { type: "n8n-nodes-base.httpRequest", name: "HTTP Request" },
      { description: { group: ["transform"], inputs: ["main"], outputs: ["main"] } },
      {}
    ),
    true
  );
});

test("isRunNodeLLMExecution detects n8n root LLM nodes", () => {
  assert.equal(
    _private.isRunNodeLLMExecution({ type: "@n8n/n8n-nodes-langchain.openAi", name: "Message a model" }),
    true
  );
  assert.equal(
    _private.isRunNodeLLMExecution({ type: "@n8n/n8n-nodes-langchain.agent", name: "AI Agent" }),
    false
  );
});

test("llmRequestFromRunNodeExecution builds messages from n8n OpenAI node parameters", () => {
  const request = _private.llmRequestFromRunNodeExecution(
    {
      data: {
        main: [[{ json: { chatInput: "写一篇文章" } }]],
      },
    },
    {
      type: "@n8n/n8n-nodes-langchain.openAi",
      parameters: {
        modelId: { value: "chatgpt-4o-latest" },
        responses: {
          values: [
            { role: "system", content: "你是一名问题分类助手" },
            { content: "={{ $json.chatInput }}" },
          ],
        },
        builtInTools: {},
      },
    }
  );

  assert.equal(request.model, "chatgpt-4o-latest");
  assert.deepEqual(request.input, [
    { role: "system", content: "你是一名问题分类助手" },
    { role: "user", content: "写一篇文章" },
  ]);
});

test("llmRequestFromRunNodeExecution resolves referenced chat trigger input", () => {
  const request = _private.llmRequestFromRunNodeExecution(
    { data: { main: [[{ json: { output: [{ content: [{ text: "1" }] }] } }]] } },
    {
      type: "@n8n/n8n-nodes-langchain.openAi",
      parameters: {
        modelId: { value: "chatgpt-4o-latest" },
        responses: {
          values: [
            { role: "system", content: "你是一名写作助手" },
            { content: "={{ $('When chat message received').item.json.chatInput }}" },
          ],
        },
      },
    },
    {
      resultData: {
        runData: {
          "When chat message received": [
            {
              data: {
                main: [[{ json: { chatInput: "写一篇文章" } }]],
              },
            },
          ],
        },
      },
    }
  );

  assert.deepEqual(request.input, [
    { role: "system", content: "你是一名写作助手" },
    { role: "user", content: "写一篇文章" },
  ]);
});

test("denormalizeResponsesInput rebuilds normalized loopback messages", () => {
  assert.deepEqual(
    _private.denormalizeResponsesInput([
      { role: "system", content: "你是一名写作助手" },
      { role: "user", content: "重写后的输入" },
    ]),
    [
      { role: "system", content: "你是一名写作助手" },
      { role: "user", content: "重写后的输入" },
    ]
  );
});

test("applyLoopbackToResponsesRequest rewrites request input only", () => {
  const request = _private.applyLoopbackToResponsesRequest(
    {
      model: "gpt-4.1",
      stream: false,
      tools: [{ type: "web_search" }],
      input: [{ role: "user", content: "原始输入" }],
    },
    JSON.stringify([
      { role: "system", content: "你是一名问题分类助手" },
      { role: "user", content: "重写后的输入" },
    ])
  );

  assert.equal(request.model, "gpt-4.1");
  assert.equal(request.stream, false);
  assert.deepEqual(request.tools, [{ type: "web_search" }]);
  assert.deepEqual(request.input, [
    { role: "system", content: "你是一名问题分类助手" },
    { role: "user", content: "重写后的输入" },
  ]);
});

test("supportsThoughtAlignmentForResponsesRequest enables stream and non-stream requests", () => {
  assert.equal(_private.supportsThoughtAlignmentForResponsesRequest({ stream: false }), true);
  assert.equal(_private.supportsThoughtAlignmentForResponsesRequest({ stream: true }), true);
});

test("supportsThoughtAlignmentForRunNode only enables OpenAI root nodes", () => {
  assert.equal(
    _private.supportsThoughtAlignmentForRunNode({ type: "@n8n/n8n-nodes-langchain.openAi" }),
    true
  );
  assert.equal(
    _private.supportsThoughtAlignmentForRunNode({ type: "@n8n/n8n-nodes-langchain.ollama" }),
    false
  );
});

test("isOpenAiTextMessageOperationNode only matches OpenAI text/message root nodes", () => {
  assert.equal(
    _private.isOpenAiTextMessageOperationNode({
      type: "@n8n/n8n-nodes-langchain.openAi",
      parameters: {
        resource: "text",
        operation: "message",
      },
    }),
    true
  );
  assert.equal(
    _private.isOpenAiTextMessageOperationNode({
      type: "@n8n/n8n-nodes-langchain.openAi",
      parameters: {
        resource: "text",
        operation: "response",
      },
    }),
    false
  );
});

test("buildThoughtAlignmentMetadata emits capability and retry fields", () => {
  assert.deepEqual(
    _private.buildThoughtAlignmentMetadata({ supported: true, retryAttempt: 1 }),
    {
      thought_regeneration_supported: true,
      thought_alignment_attempt: 1,
    }
  );
  assert.deepEqual(_private.buildThoughtAlignmentMetadata({ supported: false, retryAttempt: 0 }), {});
});

test("pluginConfigFromEnv parses JSON objects and preserves raw strings", () => {
  const oldValue = process.env.AGENTGUARD_PLUGIN_CONFIG;
  process.env.AGENTGUARD_PLUGIN_CONFIG = "{\"phases\":{\"llm_after\":{\"server\":[{\"name\":\"thought_aligner\",\"params\":{\"implementation\":\"mock\"}}]}}}";
  assert.deepEqual(_private.pluginConfigFromEnv(), {
    phases: {
      llm_after: {
        server: [{ name: "thought_aligner", params: { implementation: "mock" } }],
      },
    },
  });
  process.env.AGENTGUARD_PLUGIN_CONFIG = "/agentguard/config/plugins.thought-aligner.example.json";
  assert.equal(_private.pluginConfigFromEnv(), "/agentguard/config/plugins.thought-aligner.example.json");
  restoreEnv("AGENTGUARD_PLUGIN_CONFIG", oldValue);
});

test("guardOptions forwards AGENTGUARD_PLUGIN_CONFIG to AgentGuard", () => {
  const oldValue = process.env.AGENTGUARD_PLUGIN_CONFIG;
  process.env.AGENTGUARD_PLUGIN_CONFIG = "{\"phases\":{\"llm_after\":{\"server\":[\"thought_aligner\"]}}}";
  const options = _private.guardOptions({ workflow_id: "wf-plugin-config" }, null);
  assert.deepEqual(options.plugin_config, {
    phases: {
      llm_after: {
        server: ["thought_aligner"],
      },
    },
  });
  restoreEnv("AGENTGUARD_PLUGIN_CONFIG", oldValue);
});

test("fetchRuntimePluginConfig fetches and caches runtime plugin config", async (t) => {
  const calls = [];
  const oldFetch = global.fetch;
  const oldServerUrl = process.env.AGENTGUARD_SERVER_URL;
  const oldApiKey = process.env.AGENTGUARD_API_KEY;
  process.env.AGENTGUARD_SERVER_URL = "http://agentguard.test";
  process.env.AGENTGUARD_API_KEY = "test-api-key";
  global.fetch = async (url, options = {}) => {
    calls.push({ url: String(url), options });
    assert.match(String(options.headers.Authorization || ""), /^DPoP token-runtime-config$/);
    assert.match(String(options.headers.DPoP || ""), /^proof:GET:/);
    return {
      ok: true,
      async json() {
        return {
          status: "ok",
          plugin_config: {
            phases: {
              llm_before: { client: ["modify_input_demo"], server: [] },
            },
          },
        };
      },
    };
  };
  t.after(() => {
    global.fetch = oldFetch;
    restoreEnv("AGENTGUARD_SERVER_URL", oldServerUrl);
    restoreEnv("AGENTGUARD_API_KEY", oldApiKey);
  });

  const runtimeAuth = {
    session_id: "ags_runtime_plugin_config",
    session_token: "token-runtime-config",
    agent_id: "ag_runtime_plugin_config",
    canonical_user_id: "7",
    proof(method, url, token) {
      return `proof:${method}:${url}:${token}`;
    },
  };

  const first = await _private.fetchRuntimePluginConfig({ workflow_id: "wf-runtime-plugin-config" }, runtimeAuth);
  const second = await _private.fetchRuntimePluginConfig({ workflow_id: "wf-runtime-plugin-config" }, runtimeAuth);

  assert.deepEqual(first, {
    phases: {
      llm_before: { client: ["modify_input_demo"], server: [] },
    },
  });
  assert.deepEqual(second, first);
  assert.deepEqual(calls.map((call) => new URL(call.url).pathname), ["/v1/server/session/plugin-config"]);
});

test("isThoughtAlignmentLoopbackDecision matches protocol-tagged loopback decisions", () => {
  assert.equal(
    _private.isThoughtAlignmentLoopbackDecision(new GuardDecision({
      decision_type: DecisionType.LOOP_BACK_TO_LLM,
      processed_content: "aligned thought",
      metadata: { protocol: "thought_alignment_v1" },
    })),
    true
  );
  assert.equal(
    _private.isThoughtAlignmentLoopbackDecision(new GuardDecision({
      decision_type: DecisionType.LOOP_BACK_TO_LLM,
      processed_content: "aligned thought",
      metadata: { protocol: "something_else" },
    })),
    false
  );
});

test("applyThoughtAlignmentToResponsesRequest appends rewritten raw output message and preserves other fields", () => {
  const request = _private.applyThoughtAlignmentToResponsesRequest(
    {
      model: "gpt-4.1",
      stream: false,
      tools: [{ type: "web_search" }],
      input: [
        { role: "system", content: "原始 system" },
        { role: "user", content: "原始 user" },
      ],
    },
    {
      output: [
        {
          id: "msg_1",
          type: "message",
          role: "assistant",
          status: "completed",
          content: [{ type: "output_text", text: "raw answer", annotations: [] }],
        },
      ],
    },
    "对齐后的思维"
  );

  assert.equal(request.model, "gpt-4.1");
  assert.equal(request.stream, false);
  assert.deepEqual(request.tools, [{ type: "web_search" }]);
  assert.deepEqual(request.input, [
    { role: "system", content: "原始 system" },
    { role: "user", content: "原始 user" },
    {
      id: "msg_1",
      type: "message",
      role: "assistant",
      status: "completed",
      content: [{ type: "output_text", text: "对齐后的思维", annotations: [] }],
    },
  ]);
});

test("applyModifyToResponsesRequest rewrites request input and keeps other fields", () => {
  const request = _private.applyModifyToResponsesRequest(
    {
      model: "gpt-4.1",
      stream: false,
      tools: [{ type: "web_search" }],
      input: [{ role: "user", content: "原始输入" }],
    },
    JSON.stringify({
      model: "gpt-4.1-mini",
      input: [
        { role: "system", content: "你是一名审查助手" },
        { role: "user", content: "改写后的输入" },
      ],
    })
  );

  assert.equal(request.model, "gpt-4.1-mini");
  assert.equal(request.stream, false);
  assert.deepEqual(request.tools, [{ type: "web_search" }]);
  assert.deepEqual(request.input, [
    { role: "system", content: "你是一名审查助手" },
    { role: "user", content: "改写后的输入" },
  ]);
});

test("applyLoopbackToRunNodeArgs rewrites node response messages only", () => {
  const rewritten = _private.applyLoopbackToRunNodeArgs(
    {
      node: {
        type: "@n8n/n8n-nodes-langchain.openAi",
        parameters: {
          modelId: { value: "chatgpt-4o-latest" },
          responses: {
            values: [
              { role: "system", content: "原始 system" },
              { role: "user", content: "原始 user" },
            ],
          },
          builtInTools: { webSearch: true },
        },
      },
      executionData: {
        data: {
          main: [[{ json: { chatInput: "原始输入" } }]],
        },
      },
    },
    JSON.stringify([
      { role: "system", content: "重写 system" },
      { role: "user", content: "重写 user" },
    ])
  );

  assert.equal(rewritten.node.parameters.modelId.value, "chatgpt-4o-latest");
  assert.deepEqual(rewritten.node.parameters.builtInTools, { webSearch: true });
  assert.deepEqual(rewritten.node.parameters.responses.values, [
    { role: "system", content: "重写 system" },
    { role: "user", content: "重写 user" },
  ]);
  assert.deepEqual(rewritten.executionData.data.main[0][0].json, { chatInput: "原始输入" });
});

test("applyThoughtAlignmentToRunNodeArgs appends rewritten raw output message as assistant text and preserves node parameters", () => {
  const rewritten = _private.applyThoughtAlignmentToRunNodeArgs(
    {
      node: {
        type: "@n8n/n8n-nodes-langchain.openAi",
        parameters: {
          modelId: { value: "chatgpt-4o-latest" },
          responses: {
            values: [
              { role: "system", content: "原始 system" },
              { role: "user", content: "={{ $json.chatInput }}" },
            ],
          },
          builtInTools: { webSearch: true },
        },
      },
      executionData: {
        data: {
          main: [[{ json: { chatInput: "原始输入" } }]],
        },
      },
    },
    {
      data: [[{ json: { output: [{ type: "message", role: "assistant", content: [{ type: "output_text", text: "raw answer" }] }] } }]],
    },
    "对齐后的思维"
  );

  assert.equal(rewritten.node.parameters.modelId.value, "chatgpt-4o-latest");
  assert.deepEqual(rewritten.node.parameters.builtInTools, { webSearch: true });
  assert.deepEqual(rewritten.node.parameters.responses.values, [
    { role: "system", content: "原始 system" },
    { role: "user", content: "={{ $json.chatInput }}" },
    { role: "assistant", content: "对齐后的思维" },
  ]);
});

test("applyThoughtAlignmentToRunNodeArgs creates responses values from fallback messages when missing", () => {
  const rewritten = _private.applyThoughtAlignmentToRunNodeArgs(
    {
      node: {
        type: "@n8n/n8n-nodes-langchain.openAi",
        parameters: {
          modelId: { value: "chatgpt-4o-latest" },
        },
      },
      executionData: {
        data: {
          main: [[{ json: { chatInput: "原始输入" } }]],
        },
      },
      runExecutionData: {
        resultData: {
          runData: {},
        },
      },
    },
    "对齐后的思维"
  );

  assert.deepEqual(rewritten.node.parameters.responses.values, [
    { role: "assistant", content: "对齐后的思维" },
  ]);
});

test("applyModifyToRunNodeArgs rewrites messages and preserves other node parameters", () => {
  const rewritten = _private.applyModifyToRunNodeArgs(
    {
      node: {
        type: "@n8n/n8n-nodes-langchain.openAi",
        parameters: {
          modelId: { value: "chatgpt-4o-latest" },
          responses: {
            values: [
              { role: "system", content: "原始 system" },
              { role: "user", content: "原始 user" },
            ],
          },
          builtInTools: { webSearch: true },
        },
      },
      executionData: {
        data: {
          main: [[{ json: { chatInput: "原始输入" } }]],
        },
      },
    },
    JSON.stringify({
      modelId: { value: "gpt-4.1-mini" },
      messages: [
        { role: "system", content: "重写 system" },
        { role: "user", content: "重写 user" },
      ],
    })
  );

  assert.equal(rewritten.node.parameters.modelId.value, "gpt-4.1-mini");
  assert.deepEqual(rewritten.node.parameters.builtInTools, { webSearch: true });
  assert.deepEqual(rewritten.node.parameters.responses.values, [
    { role: "system", content: "重写 system" },
    { role: "user", content: "重写 user" },
  ]);
});

test("applyModifyToResponsesResult rewrites response output text", () => {
  const rewritten = _private.applyModifyToResponsesResult(
    {
      id: "resp_1",
      object: "response",
      status: "completed",
      output_text: "old answer",
      output: [
        {
          type: "message",
          content: [{ type: "output_text", text: "old answer", annotations: [] }],
        },
      ],
    },
    JSON.stringify({ final_output: "new answer" })
  );

  assert.equal(rewritten.id, "resp_1");
  assert.equal(rewritten.output_text, "new answer");
  assert.equal(rewritten.output[0].content[0].text, "new answer");
});

test("applyModifyToToolInput preserves runtime metadata fields", () => {
  const rewritten = _private.applyModifyToToolInput(
    {
      toolCallId: "call_1",
      sessionId: "sess_1",
      action: "execute",
      url: "https://secret.example.com",
    },
    JSON.stringify({ url: "https://safe.example.com" })
  );

  assert.equal(rewritten.toolCallId, "call_1");
  assert.equal(rewritten.sessionId, "sess_1");
  assert.equal(rewritten.action, "execute");
  assert.equal(rewritten.url, "https://safe.example.com");
});

test("applyModifyToRunNodeResult rewrites first json output field", () => {
  const rewritten = _private.applyModifyToRunNodeResult(
    {
      data: [[{ json: { output: "old answer", keep: true } }]],
      hints: ["keep"],
    },
    JSON.stringify({ output: "new answer" })
  );

  assert.deepEqual(rewritten.hints, ["keep"]);
  assert.equal(rewritten.data[0][0].json.output, "new answer");
  assert.equal(rewritten.data[0][0].json.keep, true);
});

test("llmOutputFromRunNodeResult extracts n8n OpenAI node output", () => {
  assert.deepEqual(
    _private.llmOutputFromRunNodeResult({
      data: [[{ json: { output: [{ type: "message", content: [{ type: "output_text", text: "Final answer" }] }] } }]],
    }),
    {
      output: [{ type: "message", content: [{ type: "output_text", text: "Final answer" }] }],
    }
  );
});

test("isAiToolRunNodeExecution detects n8n engine-request tool nodes", () => {
  assert.equal(
    _private.isAiToolRunNodeExecution({ name: "HTTP Request", rewireOutputLogTo: "ai_tool" }),
    true
  );
  assert.equal(
    _private.isAiToolRunNodeExecution(
      { name: "HTTP Request" },
      { node: { name: "HTTP Request", rewireOutputLogTo: "ai_tool" } }
    ),
    true
  );
  assert.equal(
    _private.isAiToolRunNodeExecution({ name: "HTTP Request", rewireOutputLogTo: "main" }),
    false
  );
});

test("aiToolArgumentsFromExecutionData reads n8n engine-request input", () => {
  assert.deepEqual(
    _private.aiToolArgumentsFromExecutionData(
      {
        data: {
          main: [[{ json: { prompt: "hello" } }]],
          ai_tool: [[{ json: { url: "https://example.com", toolCallId: "call_1" } }]],
        },
      }
    ),
    { url: "https://example.com" }
  );
  assert.deepEqual(
    _private.aiToolArgumentsFromExecutionData({
      data: {
        main: [[{ json: { city: "Paris" } }]],
      },
    }),
    { city: "Paris" }
  );
});

test("aiToolArgumentsFromExecutionData includes configured node URL", () => {
  const nodeParameters = {
    toolDescription: "Get Deepseek website information",
    curlImport: "",
    method: "GET",
    url: "https://chat.deepseek.com/",
    authentication: "none",
    provideSslCertificates: false,
    sendQuery: false,
    sendHeaders: false,
    sendBody: false,
    options: {},
    optimizeResponse: false,
    infoMessage: "",
  };
  assert.deepEqual(
    _private.aiToolArgumentsFromExecutionData(
      {
        data: {
          ai_tool: [[{ json: { action: "sendMessage", toolCallId: "call_1" } }]],
        },
      },
      {
        parameters: nodeParameters,
      }
    ),
    nodeParameters
  );
});

test("aiToolInvocationFromExecutionData moves n8n runtime fields to metadata", () => {
  const runtimeInput = {
    action: "sendMessage",
    sessionId: "session-1",
    chatInput: "hello",
    toolCallId: "call_1",
  };
  const nodeParameters = {
    toolDescription: "Get Deepseek website information",
    url: "https://chat.deepseek.com/",
    options: {},
  };
  const invocation = _private.aiToolInvocationFromExecutionData(
    {
      data: {
        ai_tool: [[{ json: runtimeInput }]],
      },
    },
    {
      parameters: nodeParameters,
    }
  );

  assert.deepEqual(invocation.arguments, nodeParameters);
  assert.equal(invocation.metadata.tool_call_id, "call_1");
  assert.equal(invocation.metadata.n8n_session_id, "session-1");
  assert.equal(invocation.metadata.n8n_action, "sendMessage");
  assert.equal(invocation.metadata.n8n_chat_input, "hello");
  assert.deepEqual(invocation.metadata.n8n_runtime_input, runtimeInput);
  assert.deepEqual(invocation.metadata.n8n_node_parameters, nodeParameters);
  assert.equal(invocation.metadata.n8n_tool_description, "Get Deepseek website information");
});

test("aiToolArgumentsFromExecutionData prefers configured node parameters over runtime input", () => {
  assert.deepEqual(
    _private.aiToolArgumentsFromExecutionData(
      {
        data: {
          ai_tool: [[{ json: { url: "https://runtime.example.com" } }]],
        },
      },
      { parameters: { url: "https://configured.example.com" } }
    ).url,
    "https://configured.example.com"
  );
});

test("inferCapabilitiesFromNode adds useful coarse capabilities", () => {
  assert.deepEqual(
    new Set(_private.inferCapabilitiesFromNode({ type: "n8n-nodes-base.httpRequest", name: "HTTP Request" }, ["n8n_node"])),
    new Set(["n8n_node", "network", "external_send"])
  );
});

test("extractWorkflowTools registers connected ai tools and ordinary nodes", () => {
  const tools = _private.extractWorkflowTools({
    nodes: [
      { name: "Chat Trigger", type: "@n8n/n8n-nodes-langchain.chatTrigger" },
      { name: "AI Agent", type: "@n8n/n8n-nodes-langchain.agent" },
      { name: "OpenAI Chat Model", type: "@n8n/n8n-nodes-langchain.lmChatOpenAi" },
      {
        id: "tool-1",
        name: "HTTP Request",
        type: "n8n-nodes-base.httpRequestTool",
        parameters: {
          toolDescription: "Fetch weather",
          placeholderDefinitions: { values: [{ name: "{city}" }] },
        },
      },
      { id: "node-1", name: "Notify API", type: "n8n-nodes-base.httpRequest", parameters: {} },
      { id: "if-1", name: "If", type: "n8n-nodes-base.if", parameters: {} },
    ],
    connections: {
      "Chat Trigger": { main: [[{ node: "AI Agent" }]] },
      "OpenAI Chat Model": { ai_languageModel: [[{ node: "AI Agent" }]] },
      "HTTP Request": { ai_tool: [[{ node: "AI Agent" }]] },
      "AI Agent": { main: [[{ node: "Notify API" }]] },
      "Notify API": { main: [[{ node: "If" }]] },
    },
  });

  assert.equal(tools.some((tool) => tool.name === "HTTP_Request"), true);
  assert.equal(tools.some((tool) => tool.name === "Notify_API"), true);
  assert.equal(tools.some((tool) => tool.name.includes(":If")), false);
  assert.deepEqual(tools.find((tool) => tool.name === "HTTP_Request").input_params, ["city"]);
  assert.equal(tools.find((tool) => tool.name === "Notify_API").metadata.node_type, "n8n-nodes-base.httpRequest");
});

test("catalogContextForWorkflow uses n8n workflow owner as user", () => {
  const { context } = _private.catalogContextForWorkflow(
    {
      id: "wf-owner-test",
      name: "Owner workflow",
      active: true,
      versionCounter: 3,
      projectId: "project-1",
      projectName: "Personal",
      ownerUserId: "user-1",
      ownerUserEmail: "owner@example.com",
      ownerUserFirstName: "Ada",
      ownerUserLastName: "Lovelace",
    },
    []
  );

  assert.equal(context.user_id, "user-1");
  assert.equal(context.agent_id, "n8n:wf-owner-test");
  assert.equal(context.metadata.n8n_user_id, "user-1");
  assert.equal(context.metadata.n8n_user_email, "owner@example.com");
  assert.equal(context.metadata.n8n_user_name, "Ada Lovelace");
  assert.equal(context.metadata.n8n_user_source, "workflow_owner");
  assert.equal(context.metadata.n8n_project_id, "project-1");
  assert.equal(context.metadata.n8n_project_name, "Personal");
});

test("registerN8nWorkflowAgent includes client plugin catalog in agent registration", async (t) => {
  const oldServerUrl = process.env.AGENTGUARD_SERVER_URL;
  const oldKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-n8n-register-"));
  process.env.AGENTGUARD_SERVER_URL = "http://agentguard.test";
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  let captured = null;

  t.after(() => {
    restoreEnv("AGENTGUARD_SERVER_URL", oldServerUrl);
    restoreEnv("AGENTGUARD_AGENT_KEY_DIR", oldKeyDir);
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const registration = await _private.registerN8nWorkflowAgent(
    {
      workflow_id: "wf-client-plugin-register",
      workflow_name: "Client Plugin Workflow",
      n8n_user_email: "owner@example.com",
    },
    {
      remote: {
        enabled: true,
        async register_agent(payload) {
          captured = payload;
          return {
            status: "ok",
            agent: {
              agent_id: "ag_n8n_client_plugins",
            },
          };
        },
      },
      reason: "runtime",
    }
  );

  assert.equal(registration.agent.agent_id, "ag_n8n_client_plugins");
  assert.ok(Array.isArray(captured.client_plugins));
  assert.equal(captured.client_plugins.some((plugin) => plugin.name === "llm_output"), true);
  assert.deepEqual(
    captured.client_plugins.find((plugin) => plugin.name === "llm_output"),
    {
      name: "llm_output",
      description: "",
      event_types: ["llm_output"],
      phases: ["llm_after"],
    }
  );
});

test("syncWorkflowCatalog registers n8n workflow agent before syncing tools", async (t) => {
  const calls = [];
  const oldFetch = global.fetch;
  const oldServerUrl = process.env.AGENTGUARD_SERVER_URL;
  const oldApiKey = process.env.AGENTGUARD_API_KEY;
  const oldKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const originalRequireRuntimeAuth = RemoteGuardClient.prototype.requireRuntimeAuth;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-n8n-keys-"));
  process.env.AGENTGUARD_SERVER_URL = "http://agentguard.test";
  process.env.AGENTGUARD_API_KEY = "test-api-key";
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  RemoteGuardClient.prototype.requireRuntimeAuth = function noopRequireRuntimeAuth() {};
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ url: String(url), options, body });
    if (String(url).endsWith("/v1/server/agents/register")) {
      assert.equal(body.provider, "n8n");
      assert.equal(body.external_agent_id, "wf-register-test");
      assert.equal(body.agent_type, "workflow");
      assert.equal(body.account_email, "owner@example.com");
      assert.equal(body.metadata.display_agent_id, "n8n:wf-register-test");
      assert.equal(body.public_key_jwk.kty, "OKP");
      assert.equal(Array.isArray(body.client_plugins), true);
      assert.equal(body.client_plugins.some((plugin) => plugin.name === "tool_invoke"), true);
      return {
        ok: true,
        json: async () => ({
          status: "ok",
          agent: {
            agent_id: "ag_canonical_n8n",
            agent_identity_code: "agic_test",
            public_key_thumbprint: body.metadata.agent_public_key_thumbprint,
          },
          credential: {},
          user_agent: { bound: true, user_id: 7 },
        }),
      };
    }
    if (String(url).endsWith("/v1/server/session/register")) {
      assert.equal(body.context.agent_id, "ag_canonical_n8n");
      assert.equal(body.context.metadata.agentguard_agent_id, "ag_canonical_n8n");
      return { ok: true, json: async () => ({ status: "ok" }) };
    }
    if (String(url).endsWith("/v1/server/tools/sync")) {
      assert.equal(body.context.agent_id, "ag_canonical_n8n");
      return { ok: true, json: async () => ({ status: "ok", tool_count: body.tools.length }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };
  t.after(() => {
    global.fetch = oldFetch;
    restoreEnv("AGENTGUARD_SERVER_URL", oldServerUrl);
    restoreEnv("AGENTGUARD_API_KEY", oldApiKey);
    restoreEnv("AGENTGUARD_AGENT_KEY_DIR", oldKeyDir);
    RemoteGuardClient.prototype.requireRuntimeAuth = originalRequireRuntimeAuth;
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const result = await _private.syncWorkflowCatalog({
    id: "wf-register-test",
    name: "Registered workflow",
    active: true,
    versionCounter: 5,
    ownerUserId: "owner-1",
    ownerUserEmail: "owner@example.com",
    nodes: [
      { id: "node-1", name: "Notify API", type: "n8n-nodes-base.httpRequest", parameters: {} },
    ],
    connections: {},
  });

  assert.equal(result.agent_id, "ag_canonical_n8n");
  assert.deepEqual(
    calls.map((call) => new URL(call.url).pathname),
    [
      "/v1/server/agents/register",
      "/v1/server/session/register",
      "/v1/server/tools/sync",
    ]
  );
});

function restoreEnv(name, value) {
  if (value === undefined) {
    delete process.env[name];
  } else {
    process.env[name] = value;
  }
}

test("buildRunNodeContext reads cached n8n workflow owner identity", () => {
  _private.cacheWorkflowIdentity({
    id: "wf-runtime-test",
    projectId: "project-2",
    projectName: "Team",
    ownerUserId: "user-2",
    ownerUserEmail: "runner@example.com",
  });

  const context = _private.buildRunNodeContext(
    { id: "wf-runtime-test", name: "Runtime workflow" },
    { node: { id: "node-1", name: "HTTP Request", type: "n8n-nodes-base.httpRequest" } },
    null,
    0,
    { executionId: "exec-1" },
    "manual"
  );

  assert.equal(context.user_id, "user-2");
  assert.equal(context.n8n_user_id, "user-2");
  assert.equal(context.n8n_user_email, "runner@example.com");
  assert.equal(context.n8n_user_source, "workflow_owner");
  assert.equal(context.n8n_project_id, "project-2");
  assert.equal(context.n8n_project_name, "Team");
  assert.equal(context.workflow_id, "wf-runtime-test");
  assert.equal(context.execution_id, "exec-1");
});

test("buildRunNodeContext uses inline workflow owner identity when cache is empty", () => {
  const context = _private.buildRunNodeContext(
    {
      id: "wf-inline-identity-test",
      name: "Inline identity workflow",
      projectId: "project-inline",
      projectName: "Inline Team",
      ownerUserId: "user-inline",
      ownerUserEmail: "inline@example.com",
      ownerUserFirstName: "Inline",
      ownerUserLastName: "Owner",
    },
    { node: { id: "node-inline", name: "HTTP Request", type: "n8n-nodes-base.httpRequest" } },
    null,
    0,
    { executionId: "exec-inline-1" },
    "manual"
  );

  assert.equal(context.user_id, "user-inline");
  assert.equal(context.n8n_user_email, "inline@example.com");
  assert.equal(context.n8n_user_name, "Inline Owner");
  assert.equal(context.n8n_project_id, "project-inline");
  assert.equal(context.n8n_project_name, "Inline Team");
});

test("buildRunNodeContext uses n8n sessionId as external session", () => {
  _private.cacheWorkflowIdentity({
    id: "wf-session-test",
    ownerUserId: "user-session",
    ownerUserEmail: "owner@example.com",
  });

  const context = _private.buildRunNodeContext(
    { id: "wf-session-test", name: "Session workflow" },
    {
      node: { id: "node-1", name: "Message a model", type: "@n8n/n8n-nodes-langchain.openAi" },
      data: {
        main: [[{ json: { sessionId: "3343066ded4b4b03babdd605c9bde44e", chatInput: "hello" } }]],
      },
    },
    null,
    0,
    { executionId: "exec-llm-1" },
    "manual"
  );

  assert.equal(context.execution_id, "exec-llm-1");
  assert.equal(context.n8n_session_id, "3343066ded4b4b03babdd605c9bde44e");
  assert.equal(context.external_session_id, "3343066ded4b4b03babdd605c9bde44e");
});

test("ensureN8nRuntimeAuth creates session from n8n sessionId before execution id", async (t) => {
  const calls = [];
  const oldFetch = global.fetch;
  const oldServerUrl = process.env.AGENTGUARD_SERVER_URL;
  const oldApiKey = process.env.AGENTGUARD_API_KEY;
  const oldKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-n8n-session-keys-"));
  process.env.AGENTGUARD_SERVER_URL = "http://agentguard.test";
  process.env.AGENTGUARD_API_KEY = "test-api-key";
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ url: String(url), body });
    if (String(url).endsWith("/v1/server/agents/register")) {
      return {
        ok: true,
        json: async () => ({
          status: "ok",
          agent: {
            agent_id: "ag_n8n_session_test",
            agent_identity_code: "agic_session_test",
            public_key_thumbprint: body.metadata.agent_public_key_thumbprint,
          },
          credential: {},
          user_agent: { bound: true, user_id: 7 },
        }),
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      assert.equal(body.provider, "n8n");
      assert.equal(body.agent_id, "ag_n8n_session_test");
      assert.equal(body.account_email, "owner@example.com");
      assert.equal(body.external_session_id, "3343066ded4b4b03babdd605c9bde44e");
      assert.equal(body.metadata.execution_id, "exec-llm-1");
      return {
        ok: true,
        json: async () => ({
          session_id: "ags_n8n_reused",
          session_token: "token",
          user_id: "7",
          expires_at: Math.floor(Date.now() / 1000) + 600,
        }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };
  t.after(() => {
    global.fetch = oldFetch;
    restoreEnv("AGENTGUARD_SERVER_URL", oldServerUrl);
    restoreEnv("AGENTGUARD_API_KEY", oldApiKey);
    restoreEnv("AGENTGUARD_AGENT_KEY_DIR", oldKeyDir);
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const auth = await _private.ensureN8nRuntimeAuth({
    workflow_id: "wf-session-auth-test",
    workflow_name: "Session auth workflow",
    execution_id: "exec-llm-1",
    n8n_session_id: "3343066ded4b4b03babdd605c9bde44e",
    n8n_user_id: "owner-1",
    n8n_user_email: "owner@example.com",
  });

  assert.equal(auth.session_id, "ags_n8n_reused");
  assert.equal(
    calls.map((call) => new URL(call.url).pathname).join(","),
    "/v1/server/agents/register,/v1/server/session/create"
  );
});

test("ensureN8nRuntimeAuth loads workflow owner identity on cache miss before creating a session", async (t) => {
  const calls = [];
  const oldFetch = global.fetch;
  const oldServerUrl = process.env.AGENTGUARD_SERVER_URL;
  const oldApiKey = process.env.AGENTGUARD_API_KEY;
  const oldKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-n8n-lazy-identity-keys-"));
  process.env.AGENTGUARD_SERVER_URL = "http://agentguard.test";
  process.env.AGENTGUARD_API_KEY = "test-api-key";
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  _private.setOpenSqliteReadOnlyForTests(() => ({
    all(sql, params, callback) {
      assert.match(sql, /WHERE COALESCE\(w\.isArchived, 0\) = 0/);
      assert.deepEqual(params, ["wf-lazy-identity-test"]);
      callback(null, [
        {
          id: "wf-lazy-identity-test",
          projectId: "project-lazy",
          projectName: "Lazy Team",
          ownerUserId: "owner-lazy",
          ownerUserEmail: "lazy-owner@example.com",
          ownerUserFirstName: "Lazy",
          ownerUserLastName: "Owner",
        },
      ]);
    },
    close(callback) {
      callback();
    },
  }));
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ url: String(url), body });
    if (String(url).endsWith("/v1/server/agents/register")) {
      assert.equal(body.account_email, "lazy-owner@example.com");
      return {
        ok: true,
        json: async () => ({
          status: "ok",
          agent: {
            agent_id: "ag_n8n_lazy_identity",
            agent_identity_code: "agic_lazy",
            public_key_thumbprint: body.metadata.agent_public_key_thumbprint,
          },
          credential: {},
          user_agent: { bound: true, user_id: 7 },
        }),
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      assert.equal(body.account_email, "lazy-owner@example.com");
      return {
        ok: true,
        json: async () => ({
          session_id: "ags_n8n_lazy_identity",
          session_token: "token-lazy",
          user_id: "7",
          expires_at: Math.floor(Date.now() / 1000) + 600,
        }),
      };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  };
  t.after(() => {
    global.fetch = oldFetch;
    restoreEnv("AGENTGUARD_SERVER_URL", oldServerUrl);
    restoreEnv("AGENTGUARD_API_KEY", oldApiKey);
    restoreEnv("AGENTGUARD_AGENT_KEY_DIR", oldKeyDir);
    _private.setOpenSqliteReadOnlyForTests(null);
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const context = {
    workflow_id: "wf-lazy-identity-test",
    workflow_name: "Lazy identity workflow",
    execution_id: "exec-lazy-1",
    n8n_session_id: "session-lazy-1",
  };
  const auth = await _private.ensureN8nRuntimeAuth(context);

  assert.equal(auth.session_id, "ags_n8n_lazy_identity");
  assert.equal(context.n8n_user_email, "lazy-owner@example.com");
  assert.equal(context.n8n_user_id, "owner-lazy");
  assert.equal(context.n8n_project_id, "project-lazy");
  assert.equal(
    calls.map((call) => new URL(call.url).pathname).join(","),
    "/v1/server/agents/register,/v1/server/session/create"
  );
});

test("getGuardForRuntime loads runtime client plugins so modify demo decisions apply locally", async (t) => {
  const { llm_input, llm_output } = require("../../../schemas/events");
  const calls = [];
  const oldFetch = global.fetch;
  const oldServerUrl = process.env.AGENTGUARD_SERVER_URL;
  const oldApiKey = process.env.AGENTGUARD_API_KEY;
  const oldKeyDir = process.env.AGENTGUARD_AGENT_KEY_DIR;
  const keyDir = fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-n8n-runtime-plugin-keys-"));
  process.env.AGENTGUARD_SERVER_URL = "http://agentguard.test";
  process.env.AGENTGUARD_API_KEY = "test-api-key";
  process.env.AGENTGUARD_AGENT_KEY_DIR = keyDir;
  global.fetch = async (url, options = {}) => {
    const body = options.body ? JSON.parse(options.body) : {};
    calls.push({ url: String(url), body });
    if (String(url).endsWith("/v1/server/agents/register")) {
      return {
        ok: true,
        json: async () => ({
          status: "ok",
          agent: {
            agent_id: "ag_n8n_runtime_plugin_guard",
            agent_identity_code: "agic_runtime_plugin_guard",
            public_key_thumbprint: body.metadata.agent_public_key_thumbprint,
          },
          credential: {},
          user_agent: { bound: true, user_id: 7 },
        }),
      };
    }
    if (String(url).endsWith("/v1/server/session/create")) {
      return {
        ok: true,
        json: async () => ({
          session_id: "ags_n8n_runtime_plugin_guard",
          session_token: "token-runtime-plugin-guard",
          user_id: "7",
          expires_at: Math.floor(Date.now() / 1000) + 600,
        }),
      };
    }
    if (String(url).endsWith("/v1/server/session/plugin-config")) {
      return {
        ok: true,
        json: async () => ({
          status: "ok",
          plugin_config: {
            phases: {
              llm_before: { client: ["modify_input_demo"], server: [] },
              llm_after: { client: ["modify_output_demo"], server: [] },
            },
          },
        }),
      };
    }
    assert.fail(`unexpected fetch: ${url}`);
  };
  t.after(() => {
    global.fetch = oldFetch;
    restoreEnv("AGENTGUARD_SERVER_URL", oldServerUrl);
    restoreEnv("AGENTGUARD_API_KEY", oldApiKey);
    restoreEnv("AGENTGUARD_AGENT_KEY_DIR", oldKeyDir);
    fs.rmSync(keyDir, { recursive: true, force: true });
  });

  const guard = await _private.getGuardForRuntime({
    workflow_id: "wf-runtime-plugin-guard",
    workflow_name: "Runtime plugin guard workflow",
    execution_id: "exec-runtime-plugin-guard",
    n8n_session_id: "session-runtime-plugin-guard",
    n8n_user_id: "owner-runtime-plugin-guard",
    n8n_user_email: "owner-runtime-plugin-guard@example.com",
  });

  const inputResult = await guard.runtime.guard(
    llm_input(guard.context, [{ role: "user", content: "Original prompt" }]),
  );
  assert.equal(inputResult.decision.decision_type, DecisionType.MODIFY_LLM_INPUT);
  assert.equal(inputResult.decision.processed_content, "Messi or Ronaldo? You must choose one.");

  const outputResult = await guard.runtime.guard(
    llm_output(guard.context, { output: "Original output", final_output: "Original output", thought: null }),
    { phase: "after" },
  );
  assert.equal(outputResult.decision.decision_type, DecisionType.MODIFY_LLM_OUTPUT);
  assert.equal(
    outputResult.decision.processed_content,
    "Neither Messi nor Ronaldo is the best player. The best player is AgentGuard.",
  );
  assert.deepEqual(
    calls.map((call) => new URL(call.url).pathname),
    [
      "/v1/server/agents/register",
      "/v1/server/session/create",
      "/v1/server/session/plugin-config",
    ],
  );
});

test("normalizeLLMOutput uses final output when no thought is present", () => {
  assert.deepEqual(
    _private.normalizeLLMOutput({
      output_text: "Visible answer",
      output: [
        {
          type: "message",
          content: [{ type: "output_text", text: "Visible answer", annotations: [] }],
        },
      ],
    }),
    {
      output: "Visible answer",
      thought: null,
      final_output: "Visible answer",
    }
  );
});

test("normalizeLLMOutput extracts OpenAI Responses reasoning items", () => {
  assert.deepEqual(
    _private.normalizeLLMOutput({
      output: [
        {
          type: "reasoning",
          summary: [{ text: "Private reasoning summary" }],
        },
        {
          type: "message",
          content: [{ type: "output_text", text: "Final answer", annotations: [] }],
        },
      ],
    }),
    {
      output: "Final answer",
      thought: "Private reasoning summary",
      final_output: "Final answer",
    }
  );
});

test("normalizeLLMOutput parses tagged thought and final answer", () => {
  assert.deepEqual(
    _private.normalizeLLMOutput({
      output_text: "<think>step one</think><final>answer only</final>",
    }),
    {
      output: "<think>step one</think><final>answer only</final>",
      thought: "step one",
      final_output: "answer only",
    }
  );
});

test("normalizeLLMOutput extracts stream deltas", () => {
  assert.deepEqual(
    _private.normalizeLLMOutput({
      output: [
        { type: "response.reasoning_summary_text.delta", delta: "thinking" },
        { type: "response.output_text.delta", delta: "Final " },
        { type: "response.output_text.delta", delta: "answer" },
      ],
    }),
    {
      output: "Final answer",
      thought: "thinking",
      final_output: "Final answer",
    }
  );
});

test("patchOpenAI retries with rewritten raw output message and retry metadata", async (t) => {
  const events = [];
  const requests = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    if (event.event_type === "llm_output" && event.metadata.thought_alignment_attempt !== 1) {
      return {
        decision: new GuardDecision({
          decision_type: DecisionType.LOOP_BACK_TO_LLM,
          reason: "align thought",
          processed_content: "对齐后的思维",
          metadata: { protocol: "thought_alignment_v1" },
        }),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class ChatOpenAIResponses {
    async completionWithRetry(request) {
      requests.push(JSON.parse(JSON.stringify(request)));
      return {
        output_text: "raw answer",
        output: [
          {
            type: "message",
            content: [{ type: "output_text", text: "raw answer", annotations: [] }],
          },
        ],
      };
    }
  }

  _private.patchOpenAI({ ChatOpenAIResponses });

  const client = new ChatOpenAIResponses();
  await client.completionWithRetry({
    model: "gpt-4.1",
    stream: false,
    sessionId: "thought-align-openai-test",
    input: [
      { role: "system", content: "原始 system" },
      { role: "user", content: "原始 user" },
    ],
  });

  assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].input, [
    { role: "system", content: "原始 system" },
    { role: "user", content: "原始 user" },
    {
      type: "message",
      content: [{ type: "output_text", text: "对齐后的思维", annotations: [] }],
    },
  ]);

  const llmInputs = events.filter((event) => event.event_type === "llm_input");
  const llmOutputs = events.filter((event) => event.event_type === "llm_output");
  assert.equal(llmInputs[0].metadata.thought_regeneration_supported, true);
  assert.equal(llmInputs[1].metadata.thought_alignment_attempt, 1);
  assert.equal(llmOutputs[0].metadata.thought_regeneration_supported, true);
  assert.equal(llmOutputs[1].metadata.thought_alignment_attempt, 1);
});

test("patchOpenAI buffers streamed output until llm_output guard runs and then replays chunks", async (t) => {
  const events = [];
  const requests = [];
  const emitted = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class ChatOpenAIResponses {
    async completionWithRetry(request) {
      requests.push(JSON.parse(JSON.stringify(request)));
      return (async function* stream() {
        emitted.push("first");
        yield { type: "response.output_text.delta", delta: "Hello " };
        emitted.push("second");
        yield { type: "response.output_text.delta", delta: "world" };
      })();
    }
  }

  _private.patchOpenAI({ ChatOpenAIResponses });

  const client = new ChatOpenAIResponses();
  const result = await client.completionWithRetry({
    model: "gpt-4.1",
    stream: true,
    sessionId: "stream-replay-test",
    input: [{ role: "user", content: "say hi" }],
  });

  assert.deepEqual(emitted, ["first", "second"]);
  assert.deepEqual(events.map((event) => event.event_type), ["llm_input", "llm_output"]);
  assert.equal(events[1].metadata.stream, true);
  assert.equal(requests.length, 1);

  const chunks = [];
  for await (const chunk of result) {
    chunks.push(chunk);
  }
  assert.deepEqual(chunks, [
    { type: "response.output_text.delta", delta: "Hello " },
    { type: "response.output_text.delta", delta: "world" },
  ]);
});

test("patchOpenAI stream modify returns rewritten synthetic stream", async (t) => {
  const events = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    if (event.event_type === "llm_output") {
      return {
        decision: GuardDecision.modify_llm_output(
          "rewrite streamed llm output",
          { processed_content: '{"output": "rewritten streamed answer"}' },
        ),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class ChatOpenAIResponses {
    async completionWithRetry() {
      return (async function* stream() {
        yield { type: "response.output_text.delta", delta: "unsafe " };
        yield { type: "response.output_text.delta", delta: "answer" };
      })();
    }
  }

  _private.patchOpenAI({ ChatOpenAIResponses });

  const client = new ChatOpenAIResponses();
  const result = await client.completionWithRetry({
    model: "gpt-4.1",
    stream: true,
    sessionId: "stream-modify-test",
    input: [{ role: "user", content: "rewrite this" }],
  });

  assert.deepEqual(events.map((event) => event.event_type), ["llm_input", "llm_output"]);

  const chunks = [];
  for await (const chunk of result) {
    chunks.push(chunk);
  }
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].delta, "rewritten streamed answer");
  assert.equal(chunks[1].response.output_text, "rewritten streamed answer");
});

test("patchOpenAI stream deny returns blocked synthetic stream without replaying original chunks", async (t) => {
  const events = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    if (event.event_type === "llm_output") {
      return {
        decision: new GuardDecision({
          decision_type: DecisionType.DENY,
          reason: "unsafe stream output",
        }),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class ChatOpenAIResponses {
    async completionWithRetry() {
      return (async function* stream() {
        yield { type: "response.output_text.delta", delta: "secret " };
        yield { type: "response.output_text.delta", delta: "data" };
      })();
    }
  }

  _private.patchOpenAI({ ChatOpenAIResponses });

  const client = new ChatOpenAIResponses();
  const result = await client.completionWithRetry({
    model: "gpt-4.1",
    stream: true,
    sessionId: "stream-deny-test",
    input: [{ role: "user", content: "leak secret" }],
  });

  assert.deepEqual(events.map((event) => event.event_type), ["llm_input", "llm_output"]);

  const chunks = [];
  for await (const chunk of result) {
    chunks.push(chunk);
  }
  assert.equal(chunks.length, 2);
  assert.equal(chunks[0].delta, "[AgentGuard blocked] unsafe stream output");
  assert.equal(chunks[1].response.output_text, "[AgentGuard blocked] unsafe stream output");
});

test("patchOpenAI stream loopback retries without leaking first attempt", async (t) => {
  const events = [];
  const requests = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    if (event.event_type === "llm_output" && event.metadata.thought_alignment_attempt !== 1) {
      return {
        decision: new GuardDecision({
          decision_type: DecisionType.LOOP_BACK_TO_LLM,
          reason: "align thought",
          processed_content: "对齐后的思维",
          metadata: { protocol: "thought_alignment_v1" },
        }),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class ChatOpenAIResponses {
    async completionWithRetry(request) {
      requests.push(JSON.parse(JSON.stringify(request)));
      const answer = requests.length === 1 ? "first answer" : "second answer";
      return (async function* stream() {
        yield { type: "response.output_text.delta", delta: answer };
        yield {
          type: "response.completed",
          response: {
            output_text: answer,
            output: [
              {
                type: "message",
                role: "assistant",
                content: [{ type: "output_text", text: answer, annotations: [] }],
              },
            ],
          },
        };
      })();
    }
  }

  _private.patchOpenAI({ ChatOpenAIResponses });

  const client = new ChatOpenAIResponses();
  const result = await client.completionWithRetry({
    model: "gpt-4.1",
    stream: true,
    sessionId: "stream-loopback-test",
    input: [{ role: "user", content: "original prompt" }],
  });

  assert.equal(requests.length, 2);
  assert.equal(requests[1].input.at(-1).content[0].text, "对齐后的思维");
  assert.equal(requests[1].input.at(-1).role, "assistant");

  const llmInputs = events.filter((event) => event.event_type === "llm_input");
  const llmOutputs = events.filter((event) => event.event_type === "llm_output");
  assert.equal(llmInputs[0].metadata.thought_regeneration_supported, true);
  assert.equal(llmInputs[1].metadata.thought_alignment_attempt, 1);
  assert.equal(llmOutputs[0].metadata.thought_regeneration_supported, true);
  assert.equal(llmOutputs[1].metadata.thought_alignment_attempt, 1);

  const chunks = [];
  for await (const chunk of result) {
    chunks.push(chunk);
  }
  assert.deepEqual(chunks, [
    { type: "response.output_text.delta", delta: "second answer" },
    {
      type: "response.completed",
      response: {
        output_text: "second answer",
        output: [
          {
            type: "message",
            role: "assistant",
            content: [{ type: "output_text", text: "second answer", annotations: [] }],
          },
        ],
      },
    },
  ]);
});

test("patchOpenAI stream errors emit llm_output error metadata and rethrow", async (t) => {
  const events = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class ChatOpenAIResponses {
    async completionWithRetry() {
      return (async function* stream() {
        yield { type: "response.output_text.delta", delta: "partial" };
        throw new Error("stream boom");
      })();
    }
  }

  _private.patchOpenAI({ ChatOpenAIResponses });

  const client = new ChatOpenAIResponses();
  await assert.rejects(
    client.completionWithRetry({
      model: "gpt-4.1",
      stream: true,
      sessionId: "stream-error-test",
      input: [{ role: "user", content: "explode" }],
    }),
    /stream boom/
  );

  assert.deepEqual(events.map((event) => event.event_type), ["llm_input", "llm_output"]);
  assert.equal(events[1].metadata.stream, true);
  assert.equal(events[1].metadata.error, "stream boom");
});

test("patchN8nCore retries runNode LLMs with rewritten raw output message and retry metadata", async (t) => {
  const events = [];
  const invocations = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    if (event.event_type === "llm_output" && event.metadata.thought_alignment_attempt !== 1) {
      return {
        decision: new GuardDecision({
          decision_type: DecisionType.LOOP_BACK_TO_LLM,
          reason: "align thought",
          processed_content: "对齐后的思维",
          metadata: { protocol: "thought_alignment_v1" },
        }),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  class WorkflowExecute {
    async runNode(workflow, executionData) {
      invocations.push({
        workflow_id: workflow.id,
        parameters: JSON.parse(JSON.stringify(executionData.node.parameters)),
      });
      return {
        data: [[{ json: { output: [{ type: "message", content: [{ type: "output_text", text: "raw answer" }] }] } }]],
        hints: [],
      };
    }
  }

  _private.patchN8nCore({ WorkflowExecute });

  const executor = new WorkflowExecute();
  await executor.runNode(
    {
      id: "wf-thought-align-test",
      name: "Thought align workflow",
      nodeTypes: {
        getByNameAndVersion: () => ({ description: { group: ["transform"], inputs: ["main"], outputs: ["main"] } }),
      },
    },
    {
      node: {
        id: "node-1",
        name: "Message a model",
        type: "@n8n/n8n-nodes-langchain.openAi",
        typeVersion: 1,
        parameters: {
          modelId: { value: "gpt-4.1" },
          responses: {
            values: [
              { role: "system", content: "原始 system" },
              { role: "user", content: "={{ $json.chatInput }}" },
            ],
          },
        },
      },
      data: {
        main: [[{ json: { sessionId: "thought-align-runnode-test", chatInput: "原始 user" } }]],
      },
    },
    null,
    0,
    { executionId: "exec-thought-align-1" },
    "manual"
  );

  assert.equal(invocations.length, 2);
  assert.deepEqual(invocations[1].parameters.responses.values, [
    { role: "system", content: "原始 system" },
    { role: "user", content: "={{ $json.chatInput }}" },
    { role: "assistant", content: "对齐后的思维" },
  ]);

  const llmInputs = events.filter((event) => event.event_type === "llm_input");
  const llmOutputs = events.filter((event) => event.event_type === "llm_output");
  assert.equal(llmInputs[0].metadata.thought_regeneration_supported, true);
  assert.equal(llmInputs[1].metadata.thought_alignment_attempt, 1);
  assert.equal(llmOutputs[0].metadata.thought_regeneration_supported, true);
  assert.equal(llmOutputs[1].metadata.thought_alignment_attempt, 1);
});

test("patchAgentToolsCommon wraps connected tool invoke", async () => {
  const calls = [];
  const tool = {
    name: "HTTP_Request",
    description: "Fetch a URL",
    metadata: { sourceNodeName: "HTTP Request" },
    invoke: async (input) => {
      calls.push(input);
      return { ok: true, input };
    },
  };
  const parserTool = {
    name: "format_final_json_response",
    invoke: async () => "parser",
  };
  const moduleExports = {
    getTools: async () => [tool, parserTool],
  };

  _private.patchAgentToolsCommon(moduleExports);
  _private.patchAgentToolsCommon(moduleExports);

  const tools = await moduleExports.getTools({
    getNode: () => ({
      id: "agent-1",
      name: "AI Agent",
      type: "@n8n/n8n-nodes-langchain.agent",
      typeVersion: 3.1,
    }),
  });
  const result = await tools[0].invoke({ url: "https://example.com" });

  assert.deepEqual(calls, [{ url: "https://example.com" }]);
  assert.deepEqual(result, { ok: true, input: { url: "https://example.com" } });
  assert.equal(await tools[1].invoke({ output: "done" }), "parser");
});

test("buildConnectedToolSourceContext captures direct LLM tool source node", () => {
  const ctx = {
    getNode: () => ({
      id: "llm-1",
      name: "Message a model",
      type: "@n8n/n8n-nodes-langchain.openAi",
      typeVersion: 2.3,
    }),
    getParentNodes: () => [
      {
        id: "tool-1",
        name: "HTTP Request",
        type: "n8n-nodes-base.httpRequestTool",
        typeVersion: 4.4,
        parameters: {
          toolDescription: "Fetch site",
          url: "https://example.com",
          options: {},
        },
      },
    ],
  };

  const context = _private.buildConnectedToolSourceContext(ctx, {
    name: "HTTP_Request",
    description: "Fetch site",
    metadata: { sourceNodeName: "HTTP Request" },
  });

  assert.equal(context.caller_node_name, "Message a model");
  assert.equal(context.caller_node_type, "@n8n/n8n-nodes-langchain.openAi");
  assert.equal(context.source_node_id, "tool-1");
  assert.equal(context.source_node_name, "HTTP Request");
  assert.equal(context.source_node_type, "n8n-nodes-base.httpRequestTool");
  assert.deepEqual(context.source_node_parameters, {
    toolDescription: "Fetch site",
    url: "https://example.com",
    options: {},
  });
});

test("buildConnectedToolSourceContext falls back to N8nTool context node parameters", () => {
  const ctx = {
    getNode: () => ({
      id: "llm-1",
      name: "Message a model",
      type: "@n8n/n8n-nodes-langchain.openAi",
      typeVersion: 2.3,
    }),
    getParentNodes: () => [],
  };
  const tool = {
    name: "HTTP_Request",
    description: "Fetch site",
    context: {
      getNode: () => ({
        id: "tool-1",
        name: "HTTP Request",
        type: "n8n-nodes-base.httpRequestTool",
        typeVersion: 4.4,
        parameters: {
          toolDescription: "获取deepseek官网信息",
          url: "https://chat.deepseek.com/",
          options: {},
        },
      }),
    },
  };

  const context = _private.buildConnectedToolSourceContext(ctx, tool);

  assert.equal(context.source_node_id, "tool-1");
  assert.equal(context.source_node_name, "HTTP Request");
  assert.equal(context.source_node_type, "n8n-nodes-base.httpRequestTool");
  assert.deepEqual(context.source_node_parameters, {
    toolDescription: "获取deepseek官网信息",
    url: "https://chat.deepseek.com/",
    options: {},
  });
});

test("sourceNodeForToolInvocation falls back to cached workflow node parameters", () => {
  _private.cacheWorkflowNodes({
    id: "wf-tool-cache-test",
    nodes: [
      {
        id: "tool-1",
        name: "HTTP Request",
        type: "n8n-nodes-base.httpRequestTool",
        typeVersion: 4.4,
        parameters: {
          toolDescription: "获取deepseek官网信息",
          url: "https://chat.deepseek.com/",
          options: {},
        },
      },
    ],
  });

  const sourceNode = _private.sourceNodeForToolInvocation({
    workflow_id: "wf-tool-cache-test",
    source_node_name: "HTTP Request",
    tool_name: "HTTP_Request",
  });

  assert.deepEqual(sourceNode.parameters, {
    toolDescription: "获取deepseek官网信息",
    url: "https://chat.deepseek.com/",
    options: {},
  });
});

test("patchConnectedToolsHelpers wraps direct LLM connected tools", async () => {
  const tool = {
    name: "HTTP_Request",
    description: "Fetch a URL",
    metadata: { sourceNodeName: "HTTP Request" },
    invoke: async (input) => ({ ok: true, input }),
  };
  const originalInvoke = tool.invoke;
  const moduleExports = {
    getConnectedTools: async () => [tool],
  };

  _private.patchConnectedToolsHelpers(moduleExports);
  _private.patchConnectedToolsHelpers(moduleExports);

  const tools = await moduleExports.getConnectedTools({
    getNode: () => ({
      id: "llm-1",
      name: "Message a model",
      type: "@n8n/n8n-nodes-langchain.openAi",
    }),
    getParentNodes: () => [
      {
        id: "tool-1",
        name: "HTTP Request",
        type: "n8n-nodes-base.httpRequestTool",
        parameters: { url: "https://configured.example.com" },
      },
    ],
  });

  assert.equal(tools[0].invoke.__agentguard_original_invoke__, originalInvoke);
  assert.deepEqual(await tools[0].invoke({ query: "hello" }), { ok: true, input: { query: "hello" } });
});

test("wrapConnectedTool is idempotent", async () => {
  let count = 0;
  const tool = {
    name: "Code",
    metadata: { sourceNodeName: "Code" },
    invoke: async (input) => {
      count += 1;
      return input;
    },
  };

  _private.wrapConnectedTool(tool);
  const firstInvoke = tool.invoke;
  _private.wrapConnectedTool(tool);

  assert.equal(tool.invoke, firstInvoke);
  assert.deepEqual(await tool.invoke({ value: 1 }), { value: 1 });
  assert.equal(count, 1);
});

test("patchOpenAiTextMessageOperation emits llm_output before tool invocation", async (t) => {
  const events = [];
  const apiBodies = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  const tool = {
    name: "HTTP_Request",
    description: "Fetch news",
    invoke: async (input) => ({ ok: true, input }),
  };
  _private.wrapConnectedTool(tool, {
    tool_name: "HTTP_Request",
    source_node_name: "HTTP Request",
    source_node_type: "n8n-nodes-base.httpRequestTool",
  });

  const moduleExports = {
    async execute() {
      throw new Error("original execute should not be called");
    },
  };

  _private.patchOpenAiTextMessageOperation(moduleExports, __filename, {
    n8nWorkflow: {
      NodeOperationError: class NodeOperationError extends Error {},
      accumulateTokenUsage() {},
      jsonParse: JSON.parse,
    },
    helpers: {
      async getConnectedTools() {
        return [tool];
      },
    },
    utils: {
      formatToOpenAIAssistantTool(currentTool) {
        return {
          type: "function",
          function: {
            name: currentTool.name,
          },
        };
      },
    },
    transport: {
      async apiRequest(_method, _path, { body }) {
        apiBodies.push(JSON.parse(JSON.stringify(body)));
        if (apiBodies.length === 1) {
          return {
            choices: [
              {
                message: {
                  role: "assistant",
                  content: "",
                  tool_calls: [
                    {
                      id: "call-1",
                      function: {
                        name: "HTTP_Request",
                        arguments: JSON.stringify({ q: "today news" }),
                      },
                    },
                  ],
                },
              },
            ],
            usage: { prompt_tokens: 1, completion_tokens: 1 },
          };
        }
        return {
          choices: [
            {
              message: {
                role: "assistant",
                content: "final answer after tool",
                tool_calls: [],
              },
            },
          ],
          usage: { prompt_tokens: 1, completion_tokens: 1 },
        };
      },
    },
  });

  const node = {
    name: "Message a model",
    type: "@n8n/n8n-nodes-langchain.openAi",
    typeVersion: 1.5,
    parameters: {
      resource: "text",
      operation: "message",
    },
  };
  const ctx = {
    getNode: () => node,
    getExecutionCancelSignal: () => null,
    getNodeParameter(name) {
      if (name === "modelId") return "gpt-4.1";
      if (name === "messages.values") return [{ role: "user", content: "today news" }];
      if (name === "options") return { maxToolsIterations: 15 };
      if (name === "options.maxToolsIterations") return 15;
      if (name === "jsonOutput") return false;
      if (name === "hideTools") return "show";
      if (name === "simplify") return true;
      return "";
    },
  };

  const result = await moduleExports.execute.call(ctx, 0);

  assert.deepEqual(
    events.map((event) => event.event_type),
    ["llm_input", "llm_output", "tool_invoke", "tool_result", "llm_input", "llm_output"]
  );
  assert.equal(events[1].payload.output.includes("HTTP_Request"), true);
  assert.equal(events[5].payload.final_output, "final answer after tool");
  assert.equal(apiBodies.length, 2);
  assert.equal(result[0].json.message.content, "final answer after tool");
});

test("patchOpenAiTextMessageOperation retries tool-call turns after llm_output thought alignment", async (t) => {
  const events = [];
  const apiBodies = [];
  const invokedTools = [];
  const originalEnforce = UGuardEnforcer.prototype.enforce;
  UGuardEnforcer.prototype.enforce = async function mockEnforce(event) {
    events.push(event.toDict());
    if (event.event_type === "llm_output" && event.metadata.thought_alignment_attempt !== 1) {
      return {
        decision: new GuardDecision({
          decision_type: DecisionType.LOOP_BACK_TO_LLM,
          reason: "align thought",
          processed_content: "重新思考后再决定",
          metadata: { protocol: "thought_alignment_v1" },
        }),
      };
    }
    return { decision: GuardDecision.allow("ok") };
  };
  t.after(() => {
    UGuardEnforcer.prototype.enforce = originalEnforce;
  });

  const unsafeTool = {
    name: "Unsafe_Tool",
    description: "Unsafe",
    invoke: async (input) => {
      invokedTools.push({ name: "Unsafe_Tool", input });
      return { ok: true, input };
    },
  };
  const safeTool = {
    name: "Safe_Tool",
    description: "Safe",
    invoke: async (input) => {
      invokedTools.push({ name: "Safe_Tool", input });
      return { ok: true, input };
    },
  };
  _private.wrapConnectedTool(unsafeTool, {
    tool_name: "Unsafe_Tool",
    source_node_name: "Unsafe Tool",
    source_node_type: "n8n-nodes-base.httpRequestTool",
  });
  _private.wrapConnectedTool(safeTool, {
    tool_name: "Safe_Tool",
    source_node_name: "Safe Tool",
    source_node_type: "n8n-nodes-base.httpRequestTool",
  });

  const moduleExports = {
    async execute() {
      throw new Error("original execute should not be called");
    },
  };

  _private.patchOpenAiTextMessageOperation(moduleExports, __filename, {
    n8nWorkflow: {
      NodeOperationError: class NodeOperationError extends Error {},
      accumulateTokenUsage() {},
      jsonParse: JSON.parse,
    },
    helpers: {
      async getConnectedTools() {
        return [unsafeTool, safeTool];
      },
    },
    utils: {
      formatToOpenAIAssistantTool(currentTool) {
        return {
          type: "function",
          function: {
            name: currentTool.name,
          },
        };
      },
    },
    transport: {
      async apiRequest(_method, _path, { body }) {
        apiBodies.push(JSON.parse(JSON.stringify(body)));
        if (apiBodies.length === 1) {
          return {
            choices: [
              {
                message: {
                  role: "assistant",
                  content: "",
                  tool_calls: [
                    {
                      id: "call-unsafe",
                      function: {
                        name: "Unsafe_Tool",
                        arguments: JSON.stringify({ q: "today news" }),
                      },
                    },
                  ],
                },
              },
            ],
            usage: { prompt_tokens: 1, completion_tokens: 1 },
          };
        }
        if (apiBodies.length === 2) {
          return {
            choices: [
              {
                message: {
                  role: "assistant",
                  content: "",
                  tool_calls: [
                    {
                      id: "call-safe",
                      function: {
                        name: "Safe_Tool",
                        arguments: JSON.stringify({ q: "today news" }),
                      },
                    },
                  ],
                },
              },
            ],
            usage: { prompt_tokens: 1, completion_tokens: 1 },
          };
        }
        return {
          choices: [
            {
              message: {
                role: "assistant",
                content: "final answer after safe tool",
                tool_calls: [],
              },
            },
          ],
          usage: { prompt_tokens: 1, completion_tokens: 1 },
        };
      },
    },
  });

  const node = {
    name: "Message a model",
    type: "@n8n/n8n-nodes-langchain.openAi",
    typeVersion: 1.5,
    parameters: {
      resource: "text",
      operation: "message",
    },
  };
  const ctx = {
    getNode: () => node,
    getExecutionCancelSignal: () => null,
    getNodeParameter(name) {
      if (name === "modelId") return "gpt-4.1";
      if (name === "messages.values") return [{ role: "user", content: "today news" }];
      if (name === "options") return { maxToolsIterations: 15 };
      if (name === "options.maxToolsIterations") return 15;
      if (name === "jsonOutput") return false;
      if (name === "hideTools") return "show";
      if (name === "simplify") return true;
      return "";
    },
  };

  const result = await moduleExports.execute.call(ctx, 0);

  assert.equal(apiBodies.length, 3);
  assert.equal(apiBodies[1].messages.at(-1).content, "重新思考后再决定");
  assert.deepEqual(
    events.map((event) => event.event_type),
    ["llm_input", "llm_output", "llm_input", "llm_output", "tool_invoke", "tool_result", "llm_input", "llm_output"]
  );
  assert.equal(events[2].metadata.thought_alignment_attempt, 1);
  assert.equal(events[3].metadata.thought_alignment_attempt, 1);
  assert.deepEqual(invokedTools.map((item) => item.name), ["Safe_Tool"]);
  assert.equal(result[0].json.message.content, "final answer after safe tool");
});
