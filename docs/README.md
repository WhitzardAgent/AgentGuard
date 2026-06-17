# AgentGuard Documentation

AgentGuard is a zero-trust security foundation for AI agents. The documentation covers deployment, plugin extension, custom-auditor extension, and runtime observability.

- [中文](zh/)：包含快速部署、`AgentGuard Client Importing`、`AgentGuard Plugins`、`Custom Plugin`、`Custom Auditor`，以及 `RuntimeEvent`、`RuntimeContext`、`trajectory_window` 的说明。
- [English](en/)： includes quick deployment, `AgentGuard Client Importing`, `AgentGuard Plugins`, `Custom Plugin`, `Custom Auditor`, and detailed explanations of `RuntimeEvent`, `RuntimeContext`, and `trajectory_window`.

## Plugin References

For implementation-level plugin details, see these repository-relative references:

- Client plugin reference: `../src/client/python/agentguard/plugins/README.md`
- Client plugin reference (中文): `../src/client/python/agentguard/plugins/README_CN.md`
- Server plugin reference: `../src/server/backend/plugins/`
- Server plugin reference (中文): `../src/server/backend/plugins/`

## Local debugging
At the **root directory** of the project, run the following command to start the local documentation server:

```bash
npm install
npm run docs:serve
```

And then you can access the documentation at `http://localhost:4000`.
