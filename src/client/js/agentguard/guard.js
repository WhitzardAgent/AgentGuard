"use strict";

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const { defaultLLMAdapters, selectLLMAdapter } = require("./adapters/llm");
const { AuditLogger } = require("./audit/logger");
const { AuditRecorder } = require("./audit/recorder");
const { PluginManager } = require("./plugins/manager");
const { ClientConfigAPIServer } = require("./config_api");
const { EventBus } = require("./harness/event_bus");
const { Lifecycle } = require("./harness/lifecycle");
const { HarnessRuntime } = require("./harness/runtime");
const { loadPolicy } = require("./rules/loader");
const { SandboxExecutor } = require("./sandbox/executor");
const { RuntimeContext } = require("./schemas/context");
const { SkillRegistryProxy } = require("./skill_client/registry_proxy");
const { RemoteSkillRunner } = require("./skill_client/remote_runner");
const { ToolMetadata } = require("./tools/metadata");
const { ToolDegradeManager } = require("./tools/degrade");
const { ToolRegistry } = require("./tools/registry");
const { ToolWrapper } = require("./tools/wrapper");
const { UGuardEnforcer } = require("./u_guard/enforcer");
const { PolicySnapshot } = require("./u_guard/policy_snapshot");
const { RemoteGuardClient } = require("./u_guard/remote_client");
const { LangChainAgentAdapter } = require("./adapters/agent/langchain");
const { AutogenAgentAdapter } = require("./adapters/agent/autogen");
const { OpenAIAgentsAdapter } = require("./adapters/agent/openai_agents");

class AgentGuard {
  constructor(session_id, options = {}) {
    const pluginPayload = pluginConfigPayload(options.checker_config || options.checkerConfig || null);
    const snapshot = this.loadSnapshot(options.policy || null);
    this.session_key = options.session_key || options.sessionKey || generateSessionKey();
    this.context = new RuntimeContext({
      session_id,
      user_id: options.user_id || options.userId || null,
      agent_id: options.agent_id || options.agentId || null,
      policy: options.policy || null,
      policy_version: snapshot.version,
      environment: options.environment || null,
      metadata: {
        client_session_key: this.session_key,
        client_checker_config: pluginPayload,
        remote_checker_config: pluginPayload,
      },
    });
    this.remote = new RemoteGuardClient(options.server_url || options.serverUrl || null, {
      api_key: options.api_key || options.apiKey || null,
      session_id: this.context.session_id,
      agent_id: this.context.agent_id,
      user_id: this.context.user_id,
      session_key: this.session_key,
      timeout_s: options.remote_timeout_s ?? options.remoteTimeoutS ?? 5.0,
      retries: options.remote_retries ?? options.remoteRetries ?? 2,
    });
    this.enforcer = new UGuardEnforcer({
      snapshot,
      remote: this.remote,
      plugin_manager: new PluginManager({ config: options.checker_config || options.checkerConfig || null }),
    });
    this.sandbox = new SandboxExecutor(options.sandbox || "local", options.sandbox_profile || options.sandboxProfile || null);
    this.audit = new AuditRecorder(session_id, new AuditLogger(options.audit_path || options.auditPath || null));
    this.registry = new ToolRegistry();
    this.degrade = new ToolDegradeManager();
    this.lifecycle = new Lifecycle();
    this.bus = new EventBus();
    this.config_api = null;
    this.runtime = new HarnessRuntime({
      context: this.context,
      enforcer: this.enforcer,
      sandbox: this.sandbox,
      audit: this.audit,
      registry: this.registry,
      degrade_manager: this.degrade,
      lifecycle: this.lifecycle,
      event_bus: this.bus,
      max_steps: options.max_steps ?? options.maxSteps ?? 12,
      max_tool_calls: options.max_tool_calls ?? options.maxToolCalls ?? 24,
      window_size: options.window_size ?? options.windowSize ?? 8,
    });
    this.llm_adapters = defaultLLMAdapters();
    this.skills = new SkillRegistryProxy({
      remote: options.server_url || options.serverUrl
        ? new RemoteSkillRunner(options.server_url || options.serverUrl, {
            api_key: options.api_key || options.apiKey || null,
            session_id: this.context.session_id,
            agent_id: this.context.agent_id,
            user_id: this.context.user_id,
            session_key: this.session_key,
          })
        : null,
    });
    this.remote_session_registration = null;
    this.remote_session_registered = false;
    this.registerRemoteSession();
  }

  loadSnapshot(policy) {
    let rules = null;
    if (policy) {
      for (const candidate of [policy, path.join("rules", "examples", `${policy}.json`), path.join("rules", `${policy}.json`)]) {
        try {
          rules = loadPolicy(candidate);
          break;
        } catch (_) {
          continue;
        }
      }
    }
    if (!rules) {
      rules = loadPolicy(null);
    }
    return new PolicySnapshot({
      version: policy || "builtin",
      rules,
    });
  }

  load_policy_snapshot(snapshot) {
    const next = snapshot instanceof PolicySnapshot ? snapshot : PolicySnapshot.fromDict(snapshot);
    this.enforcer.set_snapshot(next);
    this.context.policy_version = next.version;
  }

  update_checker_config(checker_config, { sync_remote = true, syncRemote = sync_remote } = {}) {
    const payload = pluginConfigPayload(checker_config);
    this.context.metadata.client_checker_config = payload;
    this.enforcer.update_plugin_config(checker_config);
    if (syncRemote) {
      return this.syncRemoteSession();
    }
    return Promise.resolve();
  }

  async start_config_api({ host = "127.0.0.1", port = 38181, sync_remote = true, syncRemote = sync_remote } = {}) {
    const prevConfigUrl = this.context.metadata.client_config_url;
    const prevPluginListUrl = this.context.metadata.client_plugin_list_url;
    const prevHealthUrl = this.context.metadata.client_health_url;
    if (!this.config_api) {
      this.config_api = new ClientConfigAPIServer(this, { host, port });
    }
    this.context.metadata.client_config_url = this.config_api.plugin_config_url;
    this.context.metadata.client_plugin_list_url = this.config_api.plugin_list_url;
    this.context.metadata.client_health_url = this.config_api.health_url;
    const url = await this.config_api.start().catch(() => this.config_api.plugin_config_url);
    this.context.metadata.client_config_url = url;
    this.context.metadata.client_plugin_list_url = this.config_api.plugin_list_url;
    this.context.metadata.client_health_url = this.config_api.health_url;
    const urlsChanged = (
      prevConfigUrl !== url ||
      prevPluginListUrl !== this.config_api.plugin_list_url ||
      prevHealthUrl !== this.config_api.health_url
    );
    if (syncRemote && urlsChanged) {
      await this.syncRemoteSession();
    }
    return url;
  }

  async stop_config_api() {
    if (!this.config_api) {
      return;
    }
    await this.config_api.stop();
    this.config_api = null;
    delete this.context.metadata.client_config_url;
    delete this.context.metadata.client_plugin_list_url;
    delete this.context.metadata.client_health_url;
  }

  register_tool(fn, meta = {}) {
    const metadata = meta instanceof ToolMetadata
      ? this.registry.register(fn, meta)
      : this.registry.register(fn, null, meta);
    this.reportToolMetadata(metadata);
    return metadata;
  }

  wrap_tool(fn, meta = {}) {
    const metadata = this.register_tool(fn, meta);
    return new ToolWrapper(fn, metadata, this.runtime);
  }

  wrap_llm(llm) {
    const adapter = selectLLMAdapter(llm, this.llm_adapters);
    return adapter.wrap(llm, this.runtime);
  }

  attach_autogen(agent, options = {}) {
    return new AutogenAgentAdapter().attach(agent, this, options);
  }

  attach_langchain(agent, options = {}) {
    return new LangChainAgentAdapter().attach(agent, this, options);
  }

  attach_openai_agents(agent, options = {}) {
    return new OpenAIAgentsAdapter().attach(agent, this, options);
  }

  register_skill(skill) {
    try {
      const { getRegistry } = require("./skill_client");
      getRegistry().register(skill);
    } catch (_) {
      return skill;
    }
    return skill;
  }

  async run_skill(skill_name, input_data = {}) {
    return this.skills.run(skill_name, input_data);
  }

  async invoke_tool(tool_name, arguments_ = {}) {
    const registered = this.registry.get(tool_name);
    if (!registered) {
      throw new Error(`tool not registered: ${tool_name}`);
    }
    return this.runtime.invoke_tool({
      tool_name,
      arguments: arguments_,
      fn: registered.fn,
      metadata: registered.metadata,
    });
  }

  flush_audit() {
    return this.audit.flush();
  }

  get trace() {
    return this.runtime.session.trace;
  }

  async close() {
    await this.runtime.sync_local_cache_now({ reason: "session_close" });
    if (this.remote.enabled) {
      try {
        const registered = await this.ensureRemoteSessionRegistered();
        if (registered) {
          await this.remote.unregister_session();
          this.remote_session_registered = false;
          this.remote_session_registration = null;
        }
      } catch (_) {
        // swallow remote shutdown errors to match Python close()
      }
    }
    await this.stop_config_api();
  }

  ensureRemoteSessionRegistered() {
    if (!this.remote.enabled) {
      return Promise.resolve(false);
    }
    if (this.remote_session_registered) {
      return Promise.resolve(true);
    }
    if (this.remote_session_registration) {
      return this.remote_session_registration;
    }
    this.remote_session_registration = this.remote.register_session(this.context)
      .then(() => {
        this.remote_session_registered = true;
        this.remote_session_registration = null;
        return true;
      })
      .catch(() => {
        this.remote_session_registration = null;
        return false;
      });
    return this.remote_session_registration;
  }

  syncRemoteSession() {
    if (!this.remote.enabled) {
      return Promise.resolve(false);
    }
    return this.remote.register_session(this.context)
      .then(() => {
        this.remote_session_registered = true;
        return true;
      })
      .catch(() => false);
  }

  registerRemoteSession() {
    if (!this.remote.enabled) {
      return;
    }
    if (this.remote_session_registration) {
      return;
    }
    this.remote_session_registration = this.start_config_api({ port: 0, syncRemote: false })
      .catch(() => null)
      .then(() => this.remote.register_session(this.context))
      .then(() => {
        this.remote_session_registered = true;
        this.remote_session_registration = null;
        return true;
      })
      .catch(() => {
        this.remote_session_registration = null;
        return false;
      });
  }

  reportToolMetadata(metadata) {
    if (!this.remote.enabled) {
      return;
    }
    const toolPayload = {
      name: metadata.name,
      description: metadata.description,
      input_params: [...(metadata.required_args || [])],
      capabilities: [...(metadata.capabilities || [])],
      labels: {
        boundary: String((metadata.metadata || {}).boundary || "internal"),
        sensitivity: String((metadata.metadata || {}).sensitivity || "low"),
        integrity: String((metadata.metadata || {}).integrity || "trusted"),
        tags: [ ...(((metadata.metadata || {}).tags || metadata.capabilities || []).map((tag) => String(tag)).filter(Boolean)) ],
      },
    };
    this.ensureRemoteSessionRegistered()
      .then((registered) => (registered ? this.remote.report_tool(this.context, toolPayload) : null))
      .catch(() => {});
  }
}

function generateSessionKey() {
  return `sk-${crypto.randomBytes(32).toString("base64url")}`;
}

function pluginConfigPayload(checker_config) {
  if (checker_config == null) {
    return null;
  }
  if (typeof checker_config === "object") {
    return JSON.parse(JSON.stringify(checker_config));
  }
  const raw = fs.readFileSync(checker_config, "utf-8");
  const data = JSON.parse(raw);
  if (!data || typeof data !== "object" || Array.isArray(data)) {
    throw new Error("plugin config file must contain a JSON object");
  }
  return data;
}

module.exports = {
  AgentGuard,
};
