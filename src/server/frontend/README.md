# AgentGuard Frontend Preview

This frontend preview is a small Python server that renders the static pages in `src/server/frontend/templates/` and serves JavaScript/CSS from `src/server/frontend/static/`.

Start it locally with:

```bash
./scripts/run-frontend.sh
```

The default preview URL is:

```text
http://127.0.0.1:8008
```

By default, `/api/*` requests are proxied to the real AgentGuard API at:

```text
http://127.0.0.1:38080
```

You can point the preview at another upstream API with:

```bash
export AGENTGUARD_API_BASE="http://127.0.0.1:9000"
./scripts/run-frontend.sh
```

## Structure

```text
src/server/frontend/
  app.py
  mock_backend.py
  templates/
  static/
  tests/
```

- `app.py` serves pages and static assets, and proxies API traffic by default.
- `mock_backend.py` contains the detachable mock backend for agent/tool/rule routes.
- `templates/` contains the HTML pages.
- `static/` contains frontend JavaScript and styles.
- `tests/` contains frontend preview tests.

## Mock backend

Use the detachable mock backend when the real API is inconvenient to run locally.

```bash
export AGENTGUARD_USE_MOCK="1"
./scripts/run-frontend.sh
```

When mock mode is enabled, the frontend serves these API routes from `src/server/frontend/mock_backend.py` instead of proxying upstream:

- `GET /api/tools`
- `GET /api/rules`
- `GET /api/agents/{agent_id}/tools`
- `GET /api/agents/{agent_id}/rules`
- `POST /api/rules/check`
- `POST /api/rules/reload`

Notes:

- The mock backend keeps state in memory only. Restarting `src/server/frontend/app.py` resets published rules back to the built-in sample data.
- Runtime monitor APIs are not mocked in this mode.
- Labels still use the current frontend-local save behavior; there is no mock write API for labels.

## Removing the mock backend

The mock backend is intentionally easy to remove:

1. Delete `src/server/frontend/mock_backend.py`.
2. Remove the `AGENTGUARD_USE_MOCK` switch and `_maybe_handle_mock(...)` hook from `src/server/frontend/app.py`.
3. Remove the mock-specific tests from `src/server/frontend/tests/test_app.py`.
