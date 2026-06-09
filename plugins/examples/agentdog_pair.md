# AgentDoG Paired Plugin Example

AgentDoG ships as a paired plugin:

- Client proxy: `agentguard.plugins.builtin.agentdog_proxy.AgentDoGProxyPlugin`
  maintains a redacted trajectory window and attaches it to remote requests.
- Server plugin: `backend.plugins.builtin.agentdog.AgentDoGServerPlugin`
  diagnoses the trajectory and maps risk into policy signals.

The final decision always belongs to the server `PolicyEngine`.

See `examples/agentdog_pair_demo.py` for a runnable demo.
