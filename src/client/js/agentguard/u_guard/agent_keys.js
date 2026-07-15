"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const KEY_DIR_ENV = "AGENTGUARD_AGENT_KEY_DIR";
const SEP = "\x1f";

class AgentIdentityKey {
  constructor(privateKey) {
    this.privateKey = privateKey;
  }

  get public_jwk() {
    const jwk = crypto.createPublicKey(this.privateKey).export({ format: "jwk" });
    return {
      kty: "OKP",
      crv: "Ed25519",
      x: jwk.x,
    };
  }

  get publicJwk() {
    return this.public_jwk;
  }

  get thumbprint() {
    return b64url(crypto.createHash("sha256").update(stableJson(this.public_jwk)).digest());
  }

  signSessionCreateProof({
    agent_id = null,
    agentId = agent_id,
    method,
    url,
    body,
    dpop_jkt = null,
    dpopJkt = dpop_jkt,
    ttl_seconds = 60,
    ttlSeconds = ttl_seconds,
  } = {}) {
    const now = Math.floor(Date.now() / 1000);
    const resolvedAgentId = String(agentId || "");
    const header = {
      typ: "agentguard-agent-proof+jwt",
      alg: "EdDSA",
      kid: this.thumbprint,
    };
    const payload = {
      iss: resolvedAgentId,
      sub: resolvedAgentId,
      aud: "agentguard:runtime-session-create",
      jti: `agp_${crypto.randomBytes(18).toString("base64url")}`,
      iat: now,
      exp: now + Math.max(1, Number(ttlSeconds) || 60),
      htm: String(method || "").toUpperCase(),
      htu: String(url || ""),
      body_sha256: canonicalBodySha256(body || {}),
      dpop_jkt: String(dpopJkt || ""),
    };
    const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
    const signature = crypto.sign(null, Buffer.from(signingInput, "ascii"), this.privateKey);
    return `${signingInput}.${b64url(signature)}`;
  }

  sign_session_create_proof(options) {
    return this.signSessionCreateProof(options);
  }
}

function loadOrCreateAgentKey(stableId) {
  const keyPath = keyPathFor(stableId);
  if (fs.existsSync(keyPath)) {
    return new AgentIdentityKey(crypto.createPrivateKey(fs.readFileSync(keyPath)));
  }
  fs.mkdirSync(path.dirname(keyPath), { recursive: true });
  const { privateKey } = crypto.generateKeyPairSync("ed25519");
  fs.writeFileSync(
    keyPath,
    privateKey.export({ type: "pkcs8", format: "pem" }),
    { mode: 0o600 }
  );
  try {
    fs.chmodSync(keyPath, 0o600);
  } catch (_) {
    // Best effort on platforms that do not support chmod.
  }
  return new AgentIdentityKey(privateKey);
}

function buildAgentRegistrationPayload({
  provider,
  external_agent_id = null,
  externalAgentId = external_agent_id,
  agent_type = null,
  agentType = agent_type,
  provider_instance_id = null,
  providerInstanceId = provider_instance_id,
  tenant_id = null,
  tenantId = tenant_id,
  account_email = null,
  accountEmail = account_email,
  name = null,
  description = null,
  metadata = null,
} = {}) {
  const key = loadOrCreateAgentKey(stableAgentKeyId({
    provider,
    provider_instance_id: providerInstanceId,
    tenant_id: tenantId,
    external_agent_id: externalAgentId,
    agent_type: agentType,
  }));
  return {
    provider: optionalText(provider),
    provider_instance_id: optionalText(providerInstanceId),
    tenant_id: optionalText(tenantId),
    external_agent_id: optionalText(externalAgentId),
    agent_type: optionalText(agentType),
    name: optionalText(name),
    description: optionalText(description),
    account_email: optionalText(accountEmail),
    public_key_jwk: key.public_jwk,
    metadata: {
      ...(metadata && typeof metadata === "object" && !Array.isArray(metadata) ? metadata : {}),
      agent_public_key_thumbprint: key.thumbprint,
    },
  };
}

function agentIdentityKeyId({
  provider,
  external_agent_id = null,
  externalAgentId = external_agent_id,
  agent_type = null,
  agentType = agent_type,
  provider_instance_id = null,
  providerInstanceId = provider_instance_id,
  tenant_id = null,
  tenantId = tenant_id,
} = {}) {
  return stableAgentKeyId({
    provider,
    provider_instance_id: providerInstanceId,
    tenant_id: tenantId,
    external_agent_id: externalAgentId,
    agent_type: agentType,
  });
}

function stableAgentKeyId({
  provider,
  provider_instance_id = null,
  providerInstanceId = provider_instance_id,
  tenant_id = null,
  tenantId = tenant_id,
  external_agent_id = null,
  externalAgentId = external_agent_id,
  agent_type = null,
  agentType = agent_type,
} = {}) {
  return [
    optionalText(provider),
    optionalText(providerInstanceId),
    optionalText(tenantId),
    optionalText(externalAgentId),
    optionalText(agentType),
  ].join(SEP);
}

function canonicalBodySha256(value) {
  return b64url(crypto.createHash("sha256").update(stableJson(stripNull(value))).digest());
}

function stripNull(value) {
  if (Array.isArray(value)) {
    return value.filter((item) => item != null).map(stripNull);
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .filter(([, item]) => item != null)
        .map(([key, item]) => [String(key), stripNull(item)])
    );
  }
  return value;
}

function keyPathFor(stableId) {
  const keyDir = path.resolve(
    String(process.env[KEY_DIR_ENV] || path.join(os.homedir(), ".agentguard", "agent_keys")).replace(/^~(?=$|\/)/, os.homedir())
  );
  const digest = crypto.createHash("sha256").update(String(stableId || "")).digest("hex");
  return path.join(keyDir, `${digest}.pem`);
}

function b64urlJson(value) {
  return b64url(Buffer.from(stableJson(value), "utf8"));
}

function stableJson(value) {
  return JSON.stringify(sortJson(value));
}

function sortJson(value) {
  if (Array.isArray(value)) {
    return value.map(sortJson);
  }
  if (!value || typeof value !== "object") {
    return value;
  }
  return Object.fromEntries(
    Object.keys(value).sort().map((key) => [key, sortJson(value[key])])
  );
}

function optionalText(value) {
  if (value == null) {
    return null;
  }
  const text = String(value).trim();
  return text || null;
}

function b64url(value) {
  return Buffer.from(value).toString("base64url");
}

module.exports = {
  AgentIdentityKey,
  KEY_DIR_ENV,
  agentIdentityKeyId,
  buildAgentRegistrationPayload,
  canonicalBodySha256,
  loadOrCreateAgentKey,
  stableAgentKeyId,
  _private: {
    keyPathFor,
    stableJson,
    stripNull,
  },
};
