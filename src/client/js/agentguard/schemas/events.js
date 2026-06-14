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

class RuntimeEvent {
  constructor(data = {}) {
    this.event_id = data.event_id || data.eventId || newId();
    this.event_type = data.event_type || data.eventType;
    this.timestamp = Number(data.timestamp ?? nowTs());
    this.context = data.context instanceof RuntimeContext ? data.context : RuntimeContext.fromDict(data.context || {});
    this.payload = { ...(data.payload || {}) };
    this.risk_signals = [...(data.risk_signals || data.riskSignals || [])];
    this.metadata = { ...(data.metadata || {}) };
  }

  toDict() {
    return {
      event_id: this.event_id,
      event_type: this.event_type,
      timestamp: this.timestamp,
      context: this.context.toDict(),
      payload: this.payload,
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
      payload: this.payload,
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
  return makeEvent(EventType.LLM_INPUT, context, { text, messages: [{ role: "user", content: text }] }, { metadata: meta });
}

function llm_input(context, messages, meta = {}) {
  return makeEvent(EventType.LLM_INPUT, context, { messages }, { metadata: meta });
}

function llm_output(context, output, meta = {}) {
  return makeEvent(EventType.LLM_OUTPUT, context, { output }, { metadata: meta });
}

function llm_thought(context, thought, meta = {}) {
  return llm_output(context, thought, meta);
}

function tool_invoke(context, tool_name, arguments_, options = {}) {
  return makeEvent(
    EventType.TOOL_INVOKE,
    context,
    {
      tool_name,
      arguments: arguments_,
      capabilities: options.capabilities || [],
    },
    { metadata: options.meta || options.metadata || {} }
  );
}

function tool_result(context, tool_name, result, options = {}) {
  return makeEvent(
    EventType.TOOL_RESULT,
    context,
    {
      tool_name,
      result,
      error: options.error || null,
    },
    { metadata: options.meta || options.metadata || {} }
  );
}

function final_response(context, text, meta = {}) {
  return llm_output(context, text, meta);
}

module.exports = {
  EventType,
  RuntimeEvent,
  user_input,
  llm_input,
  llm_output,
  llm_thought,
  tool_invoke,
  tool_result,
  final_response,
};
