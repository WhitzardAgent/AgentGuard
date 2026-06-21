"use strict";

class ClientSyncBuffer {
  constructor() {
    this.entries = [];
  }

  add_local_decision({ event, context, check, decision, route, extensions = {} }) {
    this.entries.push({
      source: "client_local_plugin",
      route,
      event: event.toDict(),
      context: context.toDict(),
      decision: decision.toDict(),
      plugin_result: {
        risk_signals: [...(check.risk_signals || [])],
        is_final: Boolean(check.is_final),
        decision_candidate: check.decision_candidate ? check.decision_candidate.toDict() : null,
        metadata: { ...(check.metadata || {}) },
      },
      plugin_input: {
        event: event.toDict(),
        context: context.toDict(),
      },
      extensions,
    });
  }

  has_entries() {
    return this.entries.length > 0;
  }

  snapshot() {
    return this.entries.map((entry) => ({ ...entry }));
  }

  pop_all() {
    const out = this.entries;
    this.entries = [];
    return out;
  }

  restore_front(entries) {
    if (!entries || !entries.length) {
      return;
    }
    this.entries = [...entries, ...this.entries];
  }

  remove_entries(entries) {
    const ids = new Set(
      entries
        .map((entry) => ((entry.event || {}).event_id))
        .filter(Boolean)
    );
    this.entries = this.entries.filter((entry) => !ids.has((entry.event || {}).event_id));
  }

  build_trace_upload({ context, entries, reason }) {
    return {
      session_id: context.session_id,
      agent_id: context.agent_id,
      user_id: context.user_id,
      reason,
      entries,
    };
  }
}

module.exports = {
  ClientSyncBuffer,
};
