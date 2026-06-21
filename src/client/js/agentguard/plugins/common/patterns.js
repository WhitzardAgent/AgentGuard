"use strict";

const SECRET_PATTERNS = [
  { signal: "api_key_detected", pattern: /sk-[A-Za-z0-9]{8,}/i },
  { signal: "secret_detected", pattern: /\b(api[_-]?key|secret|token|password)\b/i },
  { signal: "pii_email", pattern: /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/i },
];

function matchSignals(text) {
  const value = String(text || "");
  return SECRET_PATTERNS.filter(({ pattern }) => pattern.test(value)).map(({ signal }) => signal);
}

module.exports = {
  SECRET_PATTERNS,
  matchSignals,
};
