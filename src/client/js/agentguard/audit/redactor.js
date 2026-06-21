"use strict";

const { RuntimeEvent } = require("../schemas/events");

function redact(record) {
  if (record instanceof RuntimeEvent) {
    return record.redacted();
  }
  if (!record || typeof record !== "object") {
    return record;
  }
  const event = new RuntimeEvent({
    event_type: record.event_type || "llm_output",
    event_id: record.event_id,
    timestamp: record.timestamp,
    context: record.context || { session_id: record.session_id || "unknown" },
    payload: (record.metadata || {}).payload || {},
    metadata: record.metadata || {},
    risk_signals: record.risk_signals || [],
  }).redacted();
  return {
    ...record,
    metadata: {
      ...(record.metadata || {}),
      payload: event.payload && typeof event.payload.toDict === "function" ? event.payload.toDict() : event.payload,
      decision_metadata: event.metadata.decision_metadata || (record.metadata || {}).decision_metadata || {},
    },
  };
}

module.exports = {
  redact,
};
