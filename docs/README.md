# AgentGuard Documentation

- [中文](zh/)：包含快速部署、`AgentGuard Client Importing`、`AgentGuard Checkers`、`Custom Checker`，以及 `RuntimeEvent`、`RuntimeContext`、`trajectory_window` 的说明。
- [English](en/)： includes quick deployment, `AgentGuard Client Importing`, `AgentGuard Checkers`, `Custom Checker`, and detailed explanations of `RuntimeEvent`, `RuntimeContext`, and `trajectory_window`.

## Checker References

For implementation-level checker details, see these repository-relative references:

- Client checker reference: `../src/client/python/agentguard/checkers/README.md`
- Client checker reference (中文): `../src/client/python/agentguard/checkers/README_CN.md`
- Server checker reference: `../src/server/backend/runtime/checkers/README.md`
- Server checker reference (中文): `../src/server/backend/runtime/checkers/README_CN.md`

## Local debugging
At the **root directory** of the project, run the following command to start the local documentation server:

```bash
npm install
npm run docs:serve
```

And then you can access the documentation at `http://localhost:4000`.
