"use strict";

const { PluginManager } = require("../plugins/manager");
const { GuardDecision } = require("../schemas/decisions");
const { ClientSyncBuffer } = require("./sync_buffer");
const { RemoteGuardError } = require("../utils/errors");

class EnforcementResult {
  constructor({ decision, event, route = "local", check = null, extensions = {} }) {
    this.decision = decision;
    this.event = event;
    this.route = route;
    this.check = check;
    this.extensions = extensions;
  }
}

class UGuardEnforcer {
  constructor({ snapshot = null, remote = null, plugin_manager = null, trace_window_provider = null, sync_buffer = null } = {}) {
    this.snapshot = snapshot;
    this.remote = remote;
    this.plugins = plugin_manager || new PluginManager();
    this.trace_window_provider = trace_window_provider;
    this.sync_buffer = sync_buffer || new ClientSyncBuffer();
  }

  set_snapshot(snapshot) {
    this.snapshot = snapshot;
  }

  update_plugin_config(config) {
    this.plugins.update_config(config);
  }

  get server_available() {
    return Boolean(this.remote && this.remote.enabled && !this.remote.breaker.is_open);
  }

  async enforce(event, context, { extensions = null } = {}) {
    const check = this.plugins.run(event, context);
    const traceWindow = this.trace_window_provider ? this.trace_window_provider() : null;
    if (check.is_final && check.decision_candidate) {
      const decision = check.decision_candidate;
      decision.metadata.route = decision.metadata.route || "local_plugin";
      this.sync_buffer.add_local_decision({
        event,
        context,
        check,
        decision,
        route: "local_plugin",
        extensions: extensions || {},
      });
      return new EnforcementResult({
        decision,
        event,
        route: "local_plugin",
        check,
        extensions: extensions || {},
      });
    }
    if (this.server_available) {
      const { decision, route } = await this.decideRemote(event, context, traceWindow, extensions || {});
      return new EnforcementResult({
        decision,
        event,
        route,
        check,
        extensions: extensions || {},
      });
    }
    return new EnforcementResult({
      decision: GuardDecision.allow("No final local plugin decision and no remote server configured.", {
        risk_signals: [...(event.risk_signals || [])],
        metadata: { route: "local_no_remote" },
      }),
      event,
      route: "local_no_remote",
      check,
      extensions: extensions || {},
    });
  }

  async decideRemote(event, context, traceWindow, extensions) {
    const cachedEntries = this.sync_buffer.pop_all();
    try {
      const decision = await this.remote.decide(event, context, {
        trajectory_window: traceWindow,
        local_signals: [...(event.risk_signals || [])],
        extensions,
        client_cached_entries: cachedEntries,
      });
      decision.metadata.route = decision.metadata.route || "remote";
      return { decision, route: "remote" };
    } catch (error) {
      this.sync_buffer.restore_front(cachedEntries);
      if (!(error instanceof RemoteGuardError)) {
        throw error;
      }
      return {
        decision: GuardDecision.require_remote_review("Remote decision unavailable; event requires server judgement.", {
          risk_signals: [...(event.risk_signals || [])],
          metadata: { route: "remote_unavailable" },
        }),
        route: "remote_unavailable",
      };
    }
  }
}

module.exports = {
  EnforcementResult,
  UGuardEnforcer,
};
