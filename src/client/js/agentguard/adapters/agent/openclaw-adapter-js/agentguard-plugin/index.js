import { createRequire } from "node:module";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const require = createRequire(import.meta.url);
const { AgentGuardOpenClawBridge } = require("./bridge.cjs");

const PHASE_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["client", "server"],
  properties: {
    client: {
      type: "array",
      items: {
        anyOf: [{ type: "string" }, { type: "object" }],
      },
    },
    server: {
      type: "array",
      items: {
        anyOf: [{ type: "string" }, { type: "object" }],
      },
    },
  },
};

const CONFIG_SCHEMA = {
  type: "object",
  additionalProperties: false,
  properties: {
    serverUrl: { type: "string" },
    apiKey: { type: "string" },
    apiKeyEnvVar: { type: "string" },
    policy: { type: "string" },
    auditPath: { type: "string" },
    remoteUnavailableMode: {
      type: "string",
      enum: ["allow", "fail_closed"],
    },
    windowSize: {
      type: "integer",
      minimum: 1,
    },
    phases: {
      type: "object",
      additionalProperties: false,
      properties: {
        llm_before: PHASE_SCHEMA,
        llm_after: PHASE_SCHEMA,
        tool_before: PHASE_SCHEMA,
        tool_after: PHASE_SCHEMA,
      },
    },
    toolCapabilities: {
      type: "object",
      additionalProperties: {
        type: "array",
        items: { type: "string" },
      },
    },
    identity: {
      type: "object",
      additionalProperties: false,
      properties: {
        userId: { type: "string" },
        userIdFrom: { type: "string" },
        agentId: { type: "string" },
        agentIdFrom: { type: "string" },
        environment: { type: "string" },
      },
    },
  },
};

export default definePluginEntry({
  id: "agentguard",
  name: "AgentGuard OpenClaw Plugin",
  description: "Maps OpenClaw hooks onto AgentGuard llm/tool policy phases.",
  configSchema: CONFIG_SCHEMA,
  register(api) {
    const bridge = new AgentGuardOpenClawBridge({
      pluginId: api.id || "agentguard",
      pluginConfig: api.pluginConfig || {},
      logger: api.logger || console,
    });

    api.on("before_tool_call", (event, ctx) => bridge.runBeforeToolCall({ event, ctx }));
    api.on("after_tool_call", (event, ctx) => bridge.runAfterToolCall({ event, ctx }));
    api.on("before_agent_run", (event, ctx) => bridge.runBeforeAgentRun({ event, ctx }));
    api.on("message_sending", (event, ctx) => bridge.runMessageSending({ event, ctx }));
    api.on("session_end", (event) => bridge.clearSession(event.sessionKey));
    api.on("gateway_stop", () => bridge.clearAll());
  },
});
