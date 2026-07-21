# 参与贡献 AgentGuard

感谢你对 AgentGuard 感兴趣！本文档介绍如何搭建开发环境、我们遵循的编码/测试规范，以及如何提交你的改动。

> 面向 AI 编码助手的构建/测试说明见 [AGENTS.md](./AGENTS.md)（英文）。English contribution guide: [CONTRIBUTING.md](./CONTRIBUTING.md)。

参与本项目的所有人都应遵守我们的[行为准则](./CODE_OF_CONDUCT.md)。如果你发现了安全漏洞，请按照
[SECURITY.md](./SECURITY.md) 中的流程私下报告，而不要直接提交公开 Issue。

## 参与方式

- 通过 [GitHub Issues](https://github.com/WhitzardAgent/AgentGuard/issues/new/choose) **报告缺陷**。
- 在提交较大的 PR 之前，先通过 feature request Issue **提出功能设想**，与维护者对齐方向。
- 改进 `docs/en/**`（英文）与 `docs/zh/**`（简体中文）下的**文档**。
- 为尚未支持的智能体框架**新增适配器**（参考
  `src/client/python/agentguard/adapters/agent/` 与
  `src/client/js/agentguard/adapters/agent/`）。
- **补充或改进测试**，尤其是策略/规则引擎相关的测试。

## 开发环境搭建

### 前置依赖

- Python 3.11+
- Node.js 18+（JS 客户端 + 文档构建工具）
- Docker + Docker Compose（可选，用于完整的端到端测试，以及按 README 说明启动控制服务器）

### 克隆并安装

```bash
git clone https://github.com/WhitzardAgent/AgentGuard.git
cd AgentGuard

# Python 客户端 + 服务端（可编辑安装，附带 dev/server 附加依赖）
python -m venv .venv
source .venv/bin/activate          # Windows 下使用 .venv\Scripts\activate
pip install -e ".[dev,server]"

# JS 客户端
npm install
```

根据你的工作内容，可能还需要其它可选依赖组：`redis`、`postgres`、`neo4j`、
`dynamic`（LiteLLM）、`dify`、`sandbox`，详见 `pyproject.toml` 中的
`[project.optional-dependencies]`。

## 运行测试

```bash
# Python 测试套件
pytest -q

# 单个测试文件 / 单个测试用例
pytest -q tests/test_parser.py
pytest -q tests/test_parser.py::test_specific_case

# JS 测试套件（使用 Node 内置的测试运行器，无需额外测试框架）
node --test

# 端到端冒烟测试（若本机有 Docker 则用 Docker，否则回退到进程内 HTTP 方式）
./scripts/e2e.sh
```

## 代码检查与类型检查

```bash
ruff check src tests     # 必须完全通过
mypy src                 # 目前仅作为参考项，暂不作为强制门槛，详见 AGENTS.md
```

请在提交 PR 前本地运行 `ruff check src tests`；CI 会强制校验该项。由于代码库仍在逐步引入完整的严格类型标注，
`mypy` 目前在 CI 中仅作为**非阻断的参考检查**运行——你不需要修复所有历史遗留问题，但请避免在你修改的文件中引入新的明显类型错误。

## Pull Request 提交规范

1. **Fork** 本仓库并基于 `main` 创建功能分支（`git checkout -b feat/short-description`）。
2. 尽量让每个 PR **聚焦且小巧**——一个 PR 只做一件逻辑上独立的事，能大幅加快评审速度。
3. 针对行为变更**补充/更新测试**，尤其是涉及规则/策略引擎
   （`src/shared/rules/`、`src/server/backend/runtime/`）或客户端执行路径
   （`.../u_guard/`）的改动。
4. 更新相应**文档**（`docs/en/**`，如可行也同步更新 `docs/zh/**`；面向用户的改动请同时更新
   `README.md` / `README_CN.md`）。
5. 确保本地 `ruff check`、`pytest`、`node --test` 均能通过。
6. 使用清晰的 commit message / PR 标题，例如 `feat(langgraph): support X`、
   `fix(rules): correct trace matching for Y`、`docs: clarify Z`。
7. 在适用时关联相关 Issue（如 `Closes #123`）。
8. 维护者会审查你的 PR，可能会提出修改建议，并在 CI 通过、评审通过后合并。

## 新增框架适配器

如果你要为新的智能体框架添加支持，请参考现有适配器的结构（例如
`src/client/python/agentguard/adapters/agent/langgraph.py`），并补充：

- 实现标准 attach/patch 钩子的客户端适配器；
- 位于 `tests/`（Python）或模块同级目录（JS）的单元测试；
- 一篇简短的接入文档，放在 `docs/en/how-to-plugin/`（最好同时提供
  `docs/zh/how-to-plugin/` 版本）。

可扩展性模型详见[客户端插件指南](https://whitzard.tech/AgentGuard/zh/plugins/custom_client_plugin.html)与
[服务端插件指南](https://whitzard.tech/AgentGuard/zh/plugins/custom_server_plugin.html)。

## 许可证

提交贡献即表示你同意你的贡献将按本项目的 [GNU GPLv3 许可证](./LICENSE)进行授权。
