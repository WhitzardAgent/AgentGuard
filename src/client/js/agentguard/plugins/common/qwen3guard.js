"use strict";

const { CheckResult } = require("../base");
const { GuardDecision } = require("../../schemas/decisions");

const DEFAULT_MODEL = "Qwen3Guard-Gen-8B";
const DEFAULT_TEMPERATURE = 0.0;

function textOf(value) {
  if (value == null) {
    return "";
  }
  if (typeof value === "string") {
    return value;
  }
  if (typeof value === "number" || typeof value === "boolean" || typeof value === "bigint") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.map(textOf).filter(Boolean).join("\n");
  }
  if (typeof value !== "object") {
    return String(value);
  }
  if (typeof value.text === "string") {
    return value.text;
  }
  if (typeof value.content === "string") {
    return value.content;
  }
  if (typeof value.output === "string") {
    return value.output;
  }
  if (typeof value.message === "string") {
    return value.message;
  }
  if (Array.isArray(value.content)) {
    return textOf(value.content);
  }
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function parseQwen3GuardContent(content) {
  const source = String(content || "");
  const safetyMatch = source.match(/^Safety\s*:\s*([A-Za-z_-]+)/im);
  const categoriesMatch = source.match(/^Categories\s*:\s*(.+)$/im);
  let safety = safetyMatch ? String(safetyMatch[1] || "").trim().toLowerCase() : "unknown";
  if (!["safe", "unsafe", "controversial"].includes(safety)) {
    safety = "unknown";
  }
  const categories = categoriesMatch
    ? String(categoriesMatch[1] || "")
      .split(/[,;]/)
      .map((item) => item.trim())
      .filter((item) => item && item.toLowerCase() !== "none")
    : [];
  return { safety, categories };
}

async function postChatCompletion(plugin, messages) {
  const apiUrl = String(plugin.api_url || plugin.apiUrl || "").trim();
  const apiKey = String(plugin.api_key || plugin.apiKey || "").trim();
  const model = String(plugin.model || DEFAULT_MODEL).trim();
  const temperature = Number.isFinite(Number(plugin.temperature))
    ? Number(plugin.temperature)
    : DEFAULT_TEMPERATURE;
  const timeoutS = Number.isFinite(Number(plugin.timeout_s || plugin.timeoutS))
    ? Number(plugin.timeout_s || plugin.timeoutS)
    : 20.0;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutS * 1000);
  try {
    const response = await fetch(apiUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify({ model, messages, temperature }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    return String(payload?.choices?.[0]?.message?.content || "");
  } finally {
    clearTimeout(timeout);
  }
}

function resultFromContent(content, policyScope) {
  const parsed = parseQwen3GuardContent(content);
  const metadata = {
    qwen3guard: {
      safety: parsed.safety,
      categories: parsed.categories,
      raw_content: content,
      scope: policyScope,
    },
  };
  if (parsed.safety === "safe") {
    return new CheckResult({ is_final: false, metadata });
  }
  if (parsed.safety === "unsafe") {
    const signal = "qwen3guard_unsafe";
    return new CheckResult({
      decision_candidate: GuardDecision.deny(
        `Qwen3Guard classified ${policyScope} content as unsafe.`,
        {
          policy_id: `local:qwen3guard:${policyScope}:unsafe`,
          risk_signals: [signal],
          metadata,
        },
      ),
      risk_signals: [signal],
      is_final: true,
      metadata,
    });
  }
  const signal = parsed.safety === "controversial" ? "qwen3guard_controversial" : "qwen3guard_unknown";
  return new CheckResult({
    decision_candidate: GuardDecision.human_check(
      `Qwen3Guard classified ${policyScope} content as ${parsed.safety}; human review required.`,
      {
        policy_id: `local:qwen3guard:${policyScope}:${parsed.safety}`,
        risk_signals: [signal],
        metadata,
      },
    ),
    risk_signals: [signal],
    is_final: true,
    metadata,
  });
}

async function checkWithQwen3Guard(plugin, messages, policyScope) {
  if (!messages.length) {
    return CheckResult.empty();
  }
  const apiUrl = String(plugin.api_url || plugin.apiUrl || "").trim();
  const apiKey = String(plugin.api_key || plugin.apiKey || "").trim();
  if (!apiUrl || !apiKey) {
    return new CheckResult({
      is_final: false,
      metadata: { qwen3guard: { error: "missing_api_config" } },
    });
  }
  try {
    const content = await postChatCompletion(plugin, messages);
    return resultFromContent(content, policyScope);
  } catch (error) {
    return new CheckResult({
      is_final: false,
      metadata: { qwen3guard: { error: String(error && error.message ? error.message : error) } },
    });
  }
}

module.exports = {
  checkWithQwen3Guard,
  parseQwen3GuardContent,
  textOf,
};
