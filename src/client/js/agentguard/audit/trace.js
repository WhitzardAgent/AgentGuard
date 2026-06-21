"use strict";

class Trace {
  constructor({ session_id, sessionId } = {}) {
    this.session_id = session_id || sessionId || "unknown";
    this.entries = [];
  }

  add(event, decision = null) {
    this.entries.push({
      event,
      decision,
    });
  }

  window(size) {
    return this.entries.slice(-size).map((entry) => entry.event);
  }

  toDict() {
    return {
      session_id: this.session_id,
      entries: this.entries.map(({ event, decision }) => ({
        event: event.toDict ? event.toDict() : event,
        decision: decision && decision.toDict ? decision.toDict() : decision,
      })),
    };
  }
}

module.exports = {
  Trace,
};
