"use strict";

const crypto = require("crypto");
const fs = require("fs");
const os = require("os");
const path = require("path");

const DPOP_KEY_DIR_ENV = "AGENTGUARD_DPOP_KEY_DIR";
const AGENT_KEY_DIR_ENV = "AGENTGUARD_AGENT_KEY_DIR";

class DPoPKey {
  constructor(privateKey = null) {
    this.privateKey = privateKey || crypto.generateKeyPairSync("ec", { namedCurve: "P-256" }).privateKey;
  }

  get public_jwk() {
    const jwk = crypto.createPublicKey(this.privateKey).export({ format: "jwk" });
    return {
      kty: "EC",
      crv: "P-256",
      x: jwk.x,
      y: jwk.y,
    };
  }

  get publicJwk() {
    return this.public_jwk;
  }

  get thumbprint() {
    const jwk = this.public_jwk;
    return b64url(crypto.createHash("sha256").update(stableJson({
      crv: jwk.crv,
      kty: jwk.kty,
      x: jwk.x,
      y: jwk.y,
    })).digest());
  }

  proof(method, url, accessToken = null) {
    const header = {
      typ: "dpop+jwt",
      alg: "ES256",
      jwk: this.public_jwk,
    };
    const payload = {
      jti: `dpop_${crypto.randomBytes(18).toString("base64url")}`,
      iat: Math.floor(Date.now() / 1000),
      htm: String(method || "").toUpperCase(),
      htu: String(url || ""),
    };
    if (accessToken) {
      payload.ath = b64url(crypto.createHash("sha256").update(String(accessToken)).digest());
    }
    const signingInput = `${b64urlJson(header)}.${b64urlJson(payload)}`;
    const derSignature = crypto.sign("sha256", Buffer.from(signingInput, "ascii"), this.privateKey);
    return `${signingInput}.${b64url(derToJose(derSignature, 32))}`;
  }
}

function loadOrCreateDPoPKey(stableId) {
  const keyPath = dpopKeyPathFor(stableId);
  if (fs.existsSync(keyPath)) {
    return new DPoPKey(crypto.createPrivateKey(fs.readFileSync(keyPath)));
  }
  fs.mkdirSync(path.dirname(keyPath), { recursive: true });
  const key = new DPoPKey();
  fs.writeFileSync(
    keyPath,
    key.privateKey.export({ type: "pkcs8", format: "pem" }),
    { mode: 0o600 }
  );
  try {
    fs.chmodSync(keyPath, 0o600);
  } catch (_) {
    // Best effort on platforms that do not support chmod.
  }
  return key;
}

function dpopKeyPathFor(stableId) {
  const keyDir = path.resolve(
    String(
      process.env[DPOP_KEY_DIR_ENV] ||
      process.env[AGENT_KEY_DIR_ENV] ||
      path.join(os.homedir(), ".agentguard", "agent_keys")
    ).replace(/^~(?=$|\/)/, os.homedir())
  );
  const digest = crypto.createHash("sha256").update(String(stableId || "")).digest("hex");
  return path.join(keyDir, "dpop", `${digest}.pem`);
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

function derToJose(signature, partLength) {
  const bytes = Buffer.from(signature);
  if (bytes[0] !== 0x30) {
    throw new Error("invalid ECDSA DER signature");
  }
  let offset = 2;
  if (bytes[1] & 0x80) {
    offset = 2 + (bytes[1] & 0x7f);
  }
  if (bytes[offset] !== 0x02) {
    throw new Error("invalid ECDSA DER signature");
  }
  const rLength = bytes[offset + 1];
  const r = bytes.subarray(offset + 2, offset + 2 + rLength);
  offset += 2 + rLength;
  if (bytes[offset] !== 0x02) {
    throw new Error("invalid ECDSA DER signature");
  }
  const sLength = bytes[offset + 1];
  const s = bytes.subarray(offset + 2, offset + 2 + sLength);
  return Buffer.concat([leftPadUnsigned(r, partLength), leftPadUnsigned(s, partLength)]);
}

function leftPadUnsigned(value, length) {
  let bytes = Buffer.from(value);
  while (bytes.length > 0 && bytes[0] === 0) {
    bytes = bytes.subarray(1);
  }
  if (bytes.length > length) {
    throw new Error("ECDSA signature part is too long");
  }
  if (bytes.length === length) {
    return bytes;
  }
  return Buffer.concat([Buffer.alloc(length - bytes.length), bytes]);
}

function b64url(value) {
  return Buffer.from(value).toString("base64url");
}

module.exports = {
  DPOP_KEY_DIR_ENV,
  DPoPKey,
  loadOrCreateDPoPKey,
  _private: {
    dpopKeyPathFor,
    derToJose,
    stableJson,
  },
};
