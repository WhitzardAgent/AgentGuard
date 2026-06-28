import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { AgentGuardOpenClawBridge } = require("./bridge.cjs");

const RETRIEVE_DOC_TOOL = {
  name: "retrieve_doc",
  label: "Retrieve Document",
  description: "Retrieve a document by integer id.",
  parameters: {
    type: "object",
    additionalProperties: false,
    properties: {
      id: {
        type: "integer",
        description: "Document id to retrieve.",
      },
    },
    required: ["id"],
  },
  async execute(_toolCallId, params) {
    const rawId = params && typeof params === "object" ? params.id : undefined;
    const id = Number.isInteger(rawId) ? rawId : Number.parseInt(String(rawId ?? ""), 10);
    if (!Number.isInteger(id)) {
      throw new Error("id must be an integer");
    }

    const text = `DOC#${id}: This is a document.`;
    return {
      content: [{ type: "text", text }],
      details: { id, text },
    };
  },
};

const SEND_EMAIL_TOOL = {
  name: "send_email_to",
  label: "Send Email",
  description: "Send a document to an email address.",
  parameters: {
    type: "object",
    additionalProperties: false,
    properties: {
      doc: {
        type: "string",
        description: "Document content to send.",
      },
      addr: {
        type: "string",
        description: "Recipient email address.",
      },
    },
    required: ["doc", "addr"],
  },
  async execute(_toolCallId, params) {
    const doc = params && typeof params.doc === "string" ? params.doc.trim() : "";
    const addr = params && typeof params.addr === "string" ? params.addr.trim() : "";
    if (!doc) {
      throw new Error("doc must be a non-empty string");
    }
    if (!addr) {
      throw new Error("addr must be a non-empty string");
    }

    const text = `Email has sent to ${addr}: ${doc}`;
    return {
      content: [{ type: "text", text }],
      details: { doc, addr, text },
    };
  },
};

const CONFIG_SCHEMA = {
  validate(value) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return { ok: false, errors: ["config must be an object"] };
    }
    if (
      value.configPath !== undefined &&
      (typeof value.configPath !== "string" || !value.configPath.trim())
    ) {
      return { ok: false, errors: ["configPath must be a non-empty string"] };
    }
    return { ok: true, value };
  },
  jsonSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      configPath: { type: "string" },
    },
  },
  uiHints: {
    configPath: {
      label: "AgentGuard Config Path",
      help: "Path to the AgentGuard JSON config file used by this OpenClaw plugin.",
    },
  },
};

export default {
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

    api.registerTool(RETRIEVE_DOC_TOOL);
    api.registerTool(SEND_EMAIL_TOOL);

    api.on("before_tool_call", (event, ctx) => bridge.runBeforeToolCall({ event, ctx }));
    api.on("after_tool_call", (event, ctx) => bridge.runAfterToolCall({ event, ctx }));
    api.on("before_agent_start", (event, ctx) => bridge.runBeforeAgentRun({ event, ctx }));
    api.on("message_sending", (event, ctx) => bridge.runMessageSending({ event, ctx }));
    api.on("agent_end", (event, ctx) => bridge.runAgentEnd({ event, ctx }));
    api.on("session_end", (event) => bridge.clearSession(event.sessionKey));
    api.on("gateway_stop", () => bridge.clearAll());
  },
};
