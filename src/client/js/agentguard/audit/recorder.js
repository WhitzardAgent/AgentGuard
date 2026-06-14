"use strict";

const { AuditLogger } = require("./logger");
const { redact } = require("./redactor");
const { Trace } = require("./trace");
const { isoNow } = require("../utils/time");

class AuditRecorder {
  constructor(sessionId, logger = null) {
    this.session_id = sessionId;
    this.logger = logger || new AuditLogger();
    this.trace = new Trace({ session_id: sessionId });
  }

  record(event, decision = null, plugin_results = {}) {
    this.trace.add(event, decision);
    const record = {
      timestamp: isoNow(),
      session_id: event.context.session_id,
      event_id: event.event_id,
      event_type: event.event_type,
      decision_type: decision ? decision.decision_type : null,
      reason: decision ? decision.reason : null,
      risk_signals: [...(event.risk_signals || [])],
      policy_id: decision ? decision.policy_id : null,
      plugin_results,
      metadata: {
        payload: event.payload,
        decision_metadata: decision ? decision.metadata : {},
      },
    };
    const safe = redact(record);
    this.logger.write(safe);
    return safe;
  }

  records() {
    return this.logger.records();
  }

  flush() {
    return this.logger.flush();
  }
}

module.exports = {
  AuditRecorder,
};
