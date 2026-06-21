"use strict";

function inferRequiredArgs(fn) {
  if (typeof fn !== "function") {
    return [];
  }
  const params = extractParams(fn);
  return params.flatMap(expandParam);
}

function extractParams(fn) {
  const source = typeof fn.toString === "function" ? fn.toString() : "";
  const match = source.match(/^[^(]*\(([^)]*)\)/);
  if (match) {
    return splitTopLevel(match[1], ",");
  }
  const arrowMatch = source.match(/^\s*(?:async\s+)?([A-Za-z_$][A-Za-z0-9_$]*)\s*=>/);
  if (arrowMatch && arrowMatch[1]) {
    return [arrowMatch[1]];
  }
  return [];
}

function expandParam(param) {
  const normalized = stripDefaultValue(String(param || "").trim()).replace(/^\.\.\./, "").trim();
  if (!normalized) {
    return [];
  }
  if (normalized.startsWith("{") && normalized.endsWith("}")) {
    return extractObjectKeys(normalized);
  }
  if (normalized.startsWith("[") && normalized.endsWith("]")) {
    return [];
  }
  return [normalized];
}

function extractObjectKeys(param) {
  const inner = param.slice(1, -1).trim();
  if (!inner) {
    return [];
  }
  return splitTopLevel(inner, ",")
    .map((part) => String(part || "").trim())
    .flatMap((part) => {
      if (!part) {
        return [];
      }
      if (part.startsWith("...")) {
        return [];
      }
      const aliasMatch = part.match(/^(.+?)\s*:\s*(.+)$/);
      const target = aliasMatch ? aliasMatch[2] : part;
      const cleaned = stripDefaultValue(target).trim();
      if (!cleaned || cleaned.startsWith("{") || cleaned.startsWith("[")) {
        return [];
      }
      return [cleaned];
    });
}

function stripDefaultValue(text) {
  const [head] = splitTopLevel(text, "=");
  return String(head || "").trim();
}

function splitTopLevel(text, delimiter) {
  const parts = [];
  let current = "";
  let depth = 0;
  let quote = null;
  for (const ch of String(text || "")) {
    if (quote) {
      current += ch;
      if (ch === quote) {
        quote = null;
      }
      continue;
    }
    if (ch === "\"" || ch === "'" || ch === "`") {
      quote = ch;
      current += ch;
      continue;
    }
    if (ch === "{" || ch === "[" || ch === "(") {
      depth += 1;
      current += ch;
      continue;
    }
    if ((ch === "}" || ch === "]" || ch === ")") && depth > 0) {
      depth -= 1;
      current += ch;
      continue;
    }
    if (ch === delimiter && depth === 0) {
      if (current.trim()) {
        parts.push(current.trim());
      }
      current = "";
      continue;
    }
    current += ch;
  }
  if (current.trim()) {
    parts.push(current.trim());
  }
  return parts;
}

class ToolMetadata {
  constructor(data = {}) {
    this.name = data.name || "tool";
    this.description = data.description || "";
    this.capabilities = [...(data.capabilities || [])];
    this.required_args = [...(data.required_args || [])];
    this.degraded_to = data.degraded_to ?? null;
    this.is_async = Boolean(data.is_async);
    this.schema = { ...(data.schema || {}) };
    this.metadata = { ...(data.metadata || {}) };
  }

  toDict() {
    return {
      name: this.name,
      description: this.description,
      capabilities: [...this.capabilities],
      required_args: [...this.required_args],
      degraded_to: this.degraded_to,
      is_async: this.is_async,
      schema: { ...this.schema },
      metadata: { ...this.metadata },
    };
  }

  static infer(fn, overrides = {}) {
    const name = overrides.name || fn.name || "tool";
    const description = overrides.description || "";
    return new ToolMetadata({
      name,
      description: description.split("\n")[0],
      required_args: overrides.required_args || inferRequiredArgs(fn),
      is_async: fn && fn.constructor && fn.constructor.name === "AsyncFunction",
      capabilities: overrides.capabilities || [],
      degraded_to: overrides.degraded_to || null,
      schema: overrides.schema || {},
      metadata: overrides.metadata || {},
    });
  }
}

module.exports = {
  ToolMetadata,
};
