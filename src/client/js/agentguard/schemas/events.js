"use strict";

const crypto = require("crypto");
const { RuntimeContext } = require("./context");
const { stableHash } = require("../utils/hash");
const { nowTs } = require("../utils/time");

const EventType = Object.freeze({
  LLM_INPUT: "llm_input",
  LLM_OUTPUT: "llm_output",
  TOOL_INVOKE: "tool_invoke",
  TOOL_RESULT: "tool_result",
});

const SECRET_KEY_HINTS = [
  "password",
  "passwd",
  "secret",
  "token",
  "api_key",
  "apikey",
  "authorization",
  "access_key",
  "private_key",
];
const REDACT_PATTERNS = [/sk-[A-Za-z0-9]{8,}/g, /AKIA[0-9A-Z]{12,}/g, /ghp_[A-Za-z0-9]{20,}/g, /\b\d{13,19}\b/g];
const REDACTED = "[REDACTED]";

function redactValue(value, key = null) {
  if (value && typeof value.toDict === "function") {
    return redactValue(value.toDict());
  }
  if (key && SECRET_KEY_HINTS.some((hint) => key.toLowerCase().includes(hint))) {
    return REDACTED;
  }
  if (typeof value === "string") {
    return REDACT_PATTERNS.reduce((current, pattern) => current.replace(pattern, REDACTED), value);
  }
  if (Array.isArray(value)) {
    return value.map((item) => redactValue(item));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([childKey, childValue]) => [childKey, redactValue(childValue, childKey)]));
  }
  return value;
}

class LLMInput {
  constructor(messages = []) {
    this.messages = coerceMessages(messages);
  }

  toDict() {
    return { messages: this.messages.map((item) => ({ ...item })) };
  }

  get(key, defaultValue = undefined) {
    return Object.prototype.hasOwnProperty.call(this.toDict(), key) ? this.toDict()[key] : defaultValue;
  }
}

class LLMOutput {
  constructor(output = "") {
    this.output = coerceText(output);
  }

  toDict() {
    return { output: this.output };
  }

  get(key, defaultValue = undefined) {
    return Object.prototype.hasOwnProperty.call(this.toDict(), key) ? this.toDict()[key] : defaultValue;
  }
}

class ToolInvoke {
  constructor({ tool_name = "", arguments: arguments_ = {}, capabilities = [] } = {}) {
    this.tool_name = coerceText(tool_name);
    this.arguments = { ...(arguments_ || {}) };
    this.capabilities = (capabilities || []).map((item) => String(item));
  }

  toDict() {
    return {
      tool_name: this.tool_name,
      arguments: { ...this.arguments },
      capabilities: [...this.capabilities],
    };
  }

  get(key, defaultValue = undefined) {
    return Object.prototype.hasOwnProperty.call(this.toDict(), key) ? this.toDict()[key] : defaultValue;
  }
}

class ToolResult {
  constructor({ tool_name = "", result = "" } = {}) {
    this.tool_name = coerceText(tool_name);
    this.result = coerceText(result);
  }

  toDict() {
    return { tool_name: this.tool_name, result: this.result };
  }

  get(key, defaultValue = undefined) {
    return Object.prototype.hasOwnProperty.call(this.toDict(), key) ? this.toDict()[key] : defaultValue;
  }
}

class RuntimeEvent {
  constructor(data = {}) {
    this.event_id = data.event_id || data.eventId || newId();
    this.event_type = data.event_type || data.eventType;
    this.timestamp = Number(data.timestamp ?? nowTs());
    this.context = data.context instanceof RuntimeContext ? data.context : RuntimeContext.fromDict(data.context || {});
    this.payload = payloadFromDict(this.event_type, data.payload || {});
    this.risk_signals = [...(data.risk_signals || data.riskSignals || [])];
    this.metadata = { ...(data.metadata || {}) };
  }

  toDict() {
    return {
      event_id: this.event_id,
      event_type: this.event_type,
      timestamp: this.timestamp,
      context: this.context.toDict(),
      payload: this.payload.toDict(),
      risk_signals: [...this.risk_signals],
      metadata: { ...this.metadata },
    };
  }

  redacted() {
    return new RuntimeEvent({
      ...this.toDict(),
      payload: redactValue(this.payload),
      metadata: redactValue(this.metadata),
    });
  }

  stableHash() {
    return stableHash({
      event_type: this.event_type,
      context: {
        session_id: this.context.session_id,
        policy: this.context.policy,
        policy_version: this.context.policy_version,
      },
      payload: this.payload.toDict(),
      risk_signals: [...this.risk_signals].sort(),
    });
  }

  addSignal(signal) {
    if (signal && !this.risk_signals.includes(signal)) {
      this.risk_signals.push(signal);
    }
  }

  static fromDict(data = {}) {
    return new RuntimeEvent(data);
  }
}

function newId() {
  return `evt_${crypto.randomBytes(8).toString("hex")}`;
}

function makeEvent(eventType, context, payload = {}, options = {}) {
  return new RuntimeEvent({
    event_type: eventType,
    context,
    payload,
    metadata: options.metadata || options.meta || {},
    risk_signals: options.risk_signals || options.riskSignals || [],
  });
}

function user_input(context, text, meta = {}) {
  return makeEvent(EventType.LLM_INPUT, context, new LLMInput([{ role: "user", content: text }]), { metadata: meta });
}

function llm_input(context, messages, meta = {}) {
  return makeEvent(EventType.LLM_INPUT, context, new LLMInput(messages), { metadata: meta });
}

function llm_output(context, output, meta = {}) {
  const metadata = { ...meta };
  if (!Object.prototype.hasOwnProperty.call(metadata, "output_type")) {
    metadata.output_type = output === null ? "null" : Array.isArray(output) ? "array" : typeof output;
  }
  return makeEvent(EventType.LLM_OUTPUT, context, new LLMOutput(output), { metadata });
}

function llm_thought(context, thought, meta = {}) {
  return llm_output(context, thought, meta);
}

function tool_invoke(context, tool_name, arguments_, options = {}) {
  return makeEvent(
    EventType.TOOL_INVOKE,
    context,
    new ToolInvoke({
      tool_name,
      arguments: arguments_,
      capabilities: options.capabilities || [],
    }),
    { metadata: options.meta || options.metadata || {} }
  );
}

function tool_result(context, tool_name, result, options = {}) {
  return makeEvent(
    EventType.TOOL_RESULT,
    context,
    new ToolResult({
      tool_name,
      result,
    }),
    { metadata: { ...(options.meta || options.metadata || {}), ...(options.error ? { error: options.error } : {}) } }
  );
}

function final_response(context, text, meta = {}) {
  return llm_output(context, text, meta);
}

module.exports = {
  EventType,
  LLMInput,
  LLMOutput,
  ToolInvoke,
  ToolResult,
  RuntimeEvent,
  user_input,
  llm_input,
  llm_output,
  llm_thought,
  tool_invoke,
  tool_result,
  final_response,
};

function payloadFromDict(eventType, payload) {
  if (payload && typeof payload.toDict === "function") {
    return payload;
  }
  const data = { ...(payload || {}) };
  if (eventType === EventType.LLM_INPUT) {
    let messages = data.messages ?? data.message;
    if (messages == null && data.text != null) {
      messages = [{ role: "user", content: coerceText(data.text) }];
    }
    return new LLMInput(messages || []);
  }
  if (eventType === EventType.LLM_OUTPUT) {
    return new LLMOutput(data.output ?? data.message ?? "");
  }
  if (eventType === EventType.TOOL_INVOKE) {
    return new ToolInvoke({
      tool_name: data.tool_name,
      arguments: data.arguments || {},
      capabilities: data.capabilities || [],
    });
  }
  if (eventType === EventType.TOOL_RESULT) {
    return new ToolResult({ tool_name: data.tool_name, result: data.result });
  }
  return new LLMOutput("");
}

function coerceMessages(value) {
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (item && typeof item === "object" && !Array.isArray(item)) {
        return {
          ...item,
          role: coerceText(item.role || "user"),
          content: coerceText(item.content),
        };
      }
      return { role: "user", content: coerceText(item) };
    });
  }
  if (value && typeof value === "object") {
    return [{ ...value, role: coerceText(value.role || "user"), content: coerceText(value.content) }];
  }
  if (value == null) {
    return [];
  }
  return [{ role: "user", content: coerceText(value) }];
}

function coerceText(value) {
  if (value == null) {
    return "";
  }
  return typeof value === "string" ? value : String(value);
}
