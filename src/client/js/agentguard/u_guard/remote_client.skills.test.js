"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

test("remote guard client reports skill descriptors with scan metadata", async () => {
  const { RemoteGuardClient } = require("./remote_client");
  const calls = [];
  const originalFetch = global.fetch;
  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: "ok" };
      },
    };
  };

  try {
    const client = new RemoteGuardClient("http://server.test", {
      session_token: "runtime-token-skill-report",
      use_dpop_auth: true,
      dpop_proof_factory: (method, url, token) => `proof:${method}:${url}:${token}`,
    });

    await client.report_skills(
      {
        toDict() {
          return {
            session_id: "sess-skill-report",
            agent_id: "agent-skill-report",
            user_id: "user-skill-report",
          };
        },
      },
      [
        {
          name: "demo-skill",
          description: "Demo skill",
          source_framework: "openclaw_compatible",
        },
      ],
      {
        summary: { skill_count: 1, diagnostic_count: 0 },
      },
    );

    assert.equal(calls.length, 1);
    assert.equal(calls[0].url.endsWith("/v1/server/skills/report"), true);
    const body = JSON.parse(calls[0].options.body);
    assert.equal(body.skills.length, 1);
    assert.equal(body.skills[0].name, "demo-skill");
    assert.equal(body.scan.summary.skill_count, 1);
    assert.equal(calls[0].options.headers.Authorization, "DPoP runtime-token-skill-report");
    assert.equal(calls[0].options.headers.DPoP, "proof:POST:http://server.test/v1/server/skills/report:runtime-token-skill-report");
    assert.equal(calls[0].options.headers["X-AgentGuard-Session-Id"], undefined);
    assert.equal(calls[0].options.headers["X-AgentGuard-Agent-Id"], undefined);
    assert.equal(calls[0].options.headers["X-AgentGuard-User-Id"], undefined);
    assert.equal(calls[0].options.headers["X-AgentGuard-Session-Key"], undefined);
  } finally {
    global.fetch = originalFetch;
  }
});
