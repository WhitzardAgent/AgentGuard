import { createRequire } from "node:module";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const require = createRequire(import.meta.url);
const { AgentGuardOpenClawBridge } = require("./bridge.cjs");

const CONFIG_SCHEMA = {
  type: "object",
  additionalProperties: false,
  required: ["configPath"],
  properties: {
    configPath: { type: "string" },
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
