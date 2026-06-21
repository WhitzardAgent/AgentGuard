(function () {
  const defaults = {
    genericRequestError: "Request failed.",
    unreachableApi: "Cannot reach the AgentGuard API.",
    sidebarApiChecking: "Checking...",
    sidebarApiConnected: "Connected",
    sidebarApiPartial: "Partial",
    sidebarApiUnavailable: "Unavailable",
    sidebarToolWaiting: "Waiting for first sync",
    sidebarToolUnsynced: "Not synced yet",
  };

  window.AgentGuardText = {
    ...(window.AgentGuardText || {}),
    ...defaults,
  };
})();
