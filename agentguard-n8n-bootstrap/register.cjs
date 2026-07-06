"use strict";

const path = require("path");

const agentGuardRoot = process.env.AGENTGUARD_ROOT || "/agentguard";
const adapterPath = path.join(
  agentGuardRoot,
  "src/client/js/agentguard/adapters/agent/n8n"
);

try {
  const { installN8nAdapter } = require(adapterPath);
  const status = installN8nAdapter();
  console.warn("[agentguard:n8n] bootstrap status", status);
} catch (error) {
  console.warn("[agentguard:n8n] bootstrap failed", error && error.stack ? error.stack : String(error));
}
