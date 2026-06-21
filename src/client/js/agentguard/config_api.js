"use strict";

const http = require("http");
const fs = require("fs");
const path = require("path");
const { pluginDescriptions } = require("./plugins/registry");
const { builtinPluginEntries } = require("./plugins/manager");

const PLUGIN_CONFIG_PATH = "/v1/client/plugins/config";
const PLUGIN_LIST_PATH = "/v1/client/plugins/list";
const CLIENT_HEALTH_PATH = "/v1/client/health";

class ClientConfigAPIServer {
  constructor(guard, { host = "127.0.0.1", port = 38181 } = {}) {
    this.guard = guard;
    this.host = host;
    this.port = port;
    this.server = null;
  }

  get base_url() {
    if (!this.server || !this.server.address()) {
      return `http://${this.host}:${this.port}`;
    }
    const address = this.server.address();
    return `http://${address.address}:${address.port}`;
  }

  get plugin_config_url() {
    return `${this.base_url}${PLUGIN_CONFIG_PATH}`;
  }

  get plugin_list_url() {
    return `${this.base_url}${PLUGIN_LIST_PATH}`;
  }

  get health_url() {
    return `${this.base_url}${CLIENT_HEALTH_PATH}`;
  }

  start() {
    if (this.server) {
      return Promise.resolve(this.plugin_config_url);
    }
    this.server = http.createServer(async (req, res) => {
      try {
        if (!this.authorized(req, res)) {
          return;
        }
        if (req.method === "GET" && req.url === CLIENT_HEALTH_PATH) {
          return this.send(res, 200, {
            status: "ok",
            service: "agentguard-client-config",
            session_id: this.guard.context.session_id,
            agent_id: this.guard.context.agent_id,
            user_id: this.guard.context.user_id,
          });
        }
        if (req.method === "GET" && req.url === PLUGIN_LIST_PATH) {
          const plugins = listRegisteredPlugins();
          return this.send(res, 200, {
            status: "ok",
            plugins,
          });
        }
        if (req.method === "POST" && [PLUGIN_CONFIG_PATH].includes(req.url)) {
          const body = await readJson(req);
          const config = Object.prototype.hasOwnProperty.call(body, "path")
            ? String(body.path)
            : (body.config || body);
          try {
            await this.guard.update_plugin_config(config, { syncRemote: false });
          } catch (error) {
            return this.send(res, 400, { status: "error", error: String(error.message || error) });
          }
          return this.send(res, 200, {
            status: "ok",
            applies: "next_event",
            endpoint: PLUGIN_CONFIG_PATH,
          });
        }
        return this.send(res, 404, { error: "not found" });
      } catch (error) {
        return this.send(res, 500, { status: "error", error: String(error.message || error) });
      }
    });
    return new Promise((resolve, reject) => {
      this.server.once("error", reject);
      this.server.listen(this.port, this.host, () => {
        this.server.removeListener("error", reject);
        resolve(this.plugin_config_url);
      });
    });
  }

  stop() {
    if (!this.server) {
      return Promise.resolve();
    }
    const server = this.server;
    this.server = null;
    return new Promise((resolve) => {
      server.close(() => resolve());
    });
  }

  authorized(req, res) {
    const expected = this.guard.session_key;
    const provided = req.headers["x-agentguard-session-key"];
    if (expected && !provided) {
      this.send(res, 401, { error: "missing client session key" });
      return false;
    }
    if (expected && provided !== expected) {
      this.send(res, 403, { error: "invalid client session key" });
      return false;
    }
    return true;
  }

  send(res, code, body) {
    const data = Buffer.from(JSON.stringify(body));
    res.writeHead(code, {
      "Content-Type": "application/json",
      "Content-Length": String(data.length),
    });
    res.end(data);
  }
}

function listRegisteredPlugins() {
  const { registeredPlugins } = require("./plugins/registry");
  const descriptions = pluginDescriptions();
  const deprecated = new Set(["memory", "llm_thought", "final_response"]);
  const plugins = new Map();

  for (const plugin of builtinPluginEntries()) {
    plugins.set(plugin.name, {
      name: plugin.name,
      description: plugin.description || "",
      event_types: [...(plugin.event_types || [])],
    });
  }

  for (const [name, PluginClass] of Object.entries(registeredPlugins())) {
    const instance = new PluginClass();
    plugins.set(name, {
      name,
      description: descriptions[name] || instance.description || "",
      event_types: [...(instance.event_types || [])].map((eventType) => String(eventType || "")).filter(Boolean),
    });
  }

  return [...plugins.values()]
    .filter((plugin) => !deprecated.has(plugin.name))
    .sort((left, right) => left.name.localeCompare(right.name));
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      if (!chunks.length) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString("utf-8")));
      } catch (error) {
        reject(error);
      }
    });
    req.on("error", reject);
  });
}

module.exports = {
  ClientConfigAPIServer,
  PLUGIN_CONFIG_PATH,
  PLUGIN_LIST_PATH,
  CLIENT_HEALTH_PATH,
  listRegisteredPlugins,
};
