"use strict";

const ev = require("../schemas/events");
const { DecisionType, GuardDecision } = require("../schemas/decisions");
const { Session } = require("./session");
const { EventBus } = require("./event_bus");
const { Lifecycle } = require("./lifecycle");
const { ToolRegistry } = require("../tools/registry");
const { ToolMetadata } = require("../tools/metadata");
const { ToolDegradeManager } = require("../tools/degrade");
const { LLMInterceptor, ToolInterceptor, ToolResultInterceptor } = require("../interceptors");

const INTERCEPTORS = {
  llm_input: new LLMInterceptor(),
  llm_output: new LLMInterceptor(),
  tool_invoke: new ToolInterceptor(),
  tool_result: new ToolResultInterceptor(),
};

const HOOK_BY_TYPE = {
  llm_input: "on_llm_input",
  llm_output: "on_llm_output",
  tool_invoke: "on_tool_invoke",
  tool_result: "on_tool_result",
};

class HarnessRuntime {
  constructor({
    context,
    enforcer,
    sandbox,
    audit,
    registry = null,
    degrade_manager = null,
    lifecycle = null,
    event_bus = null,
    max_steps = 12,
    max_tool_calls = 24,
    window_size = 8,
  }) {
    this.context = context;
    this.enforcer = enforcer;
    this.sandbox = sandbox;
    this.audit = audit;
    this.registry = registry || new ToolRegistry();
    this.degrade = degrade_manager || new ToolDegradeManager();
    this.lifecycle = lifecycle || new Lifecycle();
    this.bus = event_bus || new EventBus();
    this.max_steps = max_steps;
    this.max_tool_calls = max_tool_calls;
    this.window_size = window_size;
    this.session = new Session({ context });
    this.audit.trace = this.session.trace;
    this.enforcer.trace_window_provider = () => this.session.trace.window(window_size);
  }

  intercept(event, phase) {
    const interceptor = INTERCEPTORS[event.event_type];
    if (!interceptor) {
      return event;
    }
    return phase === "before" ? interceptor.before(event, this.context) : interceptor.after(event, this.context);
  }

  async guard(event, { force_remote = false, phase = "before" } = {}) {
    const nextEvent = this.intercept(event, phase);
    this.lifecycle.dispatch("on_event", nextEvent, this.context);
    const hook = HOOK_BY_TYPE[nextEvent.event_type];
    if (hook) {
      this.lifecycle.dispatch(hook, nextEvent, this.context);
    }
    const result = await this.enforcer.enforce(nextEvent, this.context, { force_remote });
    this.audit.record(nextEvent, result.decision);
    this.bus.publish(nextEvent);
    return result;
  }

  async invoke_tool({ tool_name, arguments: arguments_, fn, metadata = null }) {
    try {
      return await this.invokeToolInner({ tool_name, arguments: arguments_, fn, metadata });
    } catch (error) {
      await this.sync_local_cache_now({ reason: "client_error" });
      throw error;
    } finally {
      this.sync_local_cache_async({ reason: "round_complete" });
    }
  }

  async invokeToolInner({ tool_name, arguments: arguments_, fn, metadata = null }) {
    const meta = metadata || this.registry.metadata(tool_name) || new ToolMetadata({ name: tool_name });
    if (this.session.tool_call_count >= this.max_tool_calls) {
      return this.safeError("tool call budget exceeded", tool_name);
    }
    this.session.inc_tool_call();
    const invokeEvent = ev.tool_invoke(this.context, tool_name, arguments_, {
      capabilities: [...(meta.capabilities || [])],
    });
    const result = await this.guard(invokeEvent);
    const decision = result.decision;
    if (decision.decision_type === DecisionType.DENY) {
      return this.safeError(decision.reason, tool_name, decision);
    }
    if (decision.requires_user || decision.requires_remote) {
      return this.pending(decision.reason, tool_name, decision);
    }
    if (decision.decision_type === DecisionType.DEGRADE) {
      return this.runDegraded(tool_name, arguments_, decision);
    }
    return this.execute(tool_name, arguments_, fn, [...(meta.capabilities || [])]);
  }

  sync_local_cache_async({ reason = "round_complete" } = {}) {
    const remote = this.enforcer.remote;
    const buffer = this.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
    }
    const entries = buffer.snapshot();
    if (!entries.length) {
      return false;
    }
    const trace = buffer.build_trace_upload({
      context: this.context,
      entries,
      reason,
    });
    remote.upload_trace_async(trace, {
      on_success: () => buffer.remove_entries(entries),
    });
    return true;
  }

  async sync_local_cache_now({ reason = "client_error" } = {}) {
    const remote = this.enforcer.remote;
    const buffer = this.enforcer.sync_buffer;
    if (!remote || !remote.enabled || !buffer || !buffer.has_entries()) {
      return false;
    }
    const entries = buffer.pop_all();
    if (!entries.length) {
      return false;
    }
    const trace = buffer.build_trace_upload({
      context: this.context,
      entries,
      reason,
    });
    try {
      await remote.upload_trace(trace);
      return true;
    } catch (_) {
      buffer.restore_front(entries);
      return false;
    }
  }

  async execute(toolName, arguments_, fn, capabilities) {
    const sandboxResult = this.sandbox.run(fn, arguments_, {
      capabilities,
      tool_name: toolName,
    });
    const resolved = sandboxResult && typeof sandboxResult.then === "function" ? await sandboxResult : sandboxResult;
    if (!resolved.success) {
      const errorEvent = ev.tool_result(this.context, toolName, null, { error: resolved.error });
      await this.guard(errorEvent, { phase: "after" });
      return this.safeError(resolved.error || "tool failed", toolName);
    }
    const resultEvent = ev.tool_result(this.context, toolName, resolved.value);
    const guardResult = await this.guard(resultEvent, { phase: "after" });
    const decision = guardResult.decision;
    if (decision.decision_type === DecisionType.DENY) {
      return this.safeError(decision.reason, toolName, decision);
    }
    if (decision.decision_type === DecisionType.SANITIZE) {
      return { agentguard: "sanitized", reason: decision.reason, tool: toolName };
    }
    if (decision.requires_user || decision.requires_remote) {
      return this.pending(decision.reason, toolName, decision);
    }
    return resolved.value;
  }

  runDegraded(toolName, arguments_, decision) {
    const plan = this.degrade.plan(toolName, arguments_, decision.reason);
    if (!plan.degraded || !plan.target_tool) {
      return this.safeError(plan.safe_error || "degradation failed", toolName, decision);
    }
    const target = this.registry.get(plan.target_tool);
    if (!target) {
      return {
        agentguard: "degraded",
        tool: toolName,
        degraded_to: plan.target_tool,
        explanation: plan.explanation,
      };
    }
    const sandboxResult = this.sandbox.run(target.fn, plan.arguments, {
      capabilities: [...(target.metadata.capabilities || [])],
      tool_name: plan.target_tool,
    });
    return sandboxResult.success ? sandboxResult.value : this.safeError(sandboxResult.error || "degraded tool failed", toolName);
  }

  safeError(reason, tool, decision = null) {
    return {
      agentguard: "blocked",
      tool,
      reason,
      decision: decision ? decision.decision_type : GuardDecision.deny(reason).decision_type,
    };
  }

  pending(reason, tool, decision) {
    return {
      agentguard: "pending",
      tool,
      reason,
      decision: decision.decision_type,
    };
  }
}

module.exports = {
  HarnessRuntime,
};
