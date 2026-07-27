import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const { AgentGuardOpenCodeBridge } = require("./bridge.cjs");

const shutdownState = globalThis.__AGENTGUARD_OPENCODE_SHUTDOWN__ || {
  bridges: new Set(),
  installed: false,
  closing: false,
};
globalThis.__AGENTGUARD_OPENCODE_SHUTDOWN__ = shutdownState;

function registerBridgeForShutdown(bridge) {
  shutdownState.bridges.add(bridge);
  if (shutdownState.installed) {
    return;
  }
  shutdownState.installed = true;
  for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
    process.once(signal, () => {
      void closeAllBridges().finally(() => {
        const code = signal === "SIGINT" ? 130 : signal === "SIGTERM" ? 143 : 129;
        process.exit(code);
      });
    });
  }
}

async function closeAllBridges() {
  if (shutdownState.closing) {
    return;
  }
  shutdownState.closing = true;
  const bridges = [...shutdownState.bridges];
  await Promise.race([
    Promise.allSettled(bridges.map((bridge) => bridge.dispose())),
    new Promise((resolve) => setTimeout(resolve, 2000)),
  ]);
  shutdownState.bridges.clear();
}

export async function AgentGuardOpenCodePlugin(input, options = {}) {
  const bridge = new AgentGuardOpenCodeBridge({
    pluginConfig: options || {},
    opencode: input || {},
    logger: console,
  });
  registerBridgeForShutdown(bridge);
  void bridge.startRuntimeAuthSession();

  return {
    config: (config) => bridge.onConfig(config),
    "tool.execute.before": (hookInput, hookOutput) =>
      bridge.runToolExecuteBefore({ input: hookInput, output: hookOutput }),
    "tool.execute.after": (hookInput, hookOutput) =>
      bridge.runToolExecuteAfter({ input: hookInput, output: hookOutput }),
    "experimental.chat.messages.transform": (hookInput, hookOutput) =>
      bridge.runChatMessagesTransform({ input: hookInput, output: hookOutput }),
    "experimental.text.complete": (hookInput, hookOutput) =>
      bridge.runTextComplete({ input: hookInput, output: hookOutput }),
    "tool.definition": (hookInput, hookOutput) =>
      bridge.runToolDefinition({ input: hookInput, output: hookOutput }),
    event: (hookInput) => bridge.runEvent(hookInput),
    dispose: async () => {
      shutdownState.bridges.delete(bridge);
      await bridge.dispose();
    },
  };
}

export default AgentGuardOpenCodePlugin;
