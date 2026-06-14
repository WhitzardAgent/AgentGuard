"use strict";

const { ClientPlugin } = require("../../base");
const { formatTrajectoryWindow } = require("./formatter");

class AgentDoGProxyPlugin extends ClientPlugin {
  on_before_remote_decision(request) {
    return {
      plugin_extensions: {
        agentdog_proxy: {
          trajectory_window: formatTrajectoryWindow(request.trajectory_window || []),
        },
      },
    };
  }
}

module.exports = {
  AgentDoGProxyPlugin,
};
