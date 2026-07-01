"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  classifyMcpFile,
  detectMcpSdkUsage,
  findMcpConfigFiles,
  normalizeTransport,
  resolveScanPath,
  scanMcpConfigFile,
  scanMcpConfigs,
  scanMcpServerDescriptor,
  scanMcpSourceDirectory,
} = require("./mcp_scanner.cjs");

function makeTempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-mcp-scan-"));
}

function writeFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content);
}

function writeJson(filePath, value) {
  writeFile(filePath, JSON.stringify(value, null, 2));
}

function byPath(files) {
  return Object.fromEntries(files.map((file) => [file.relative_path, file]));
}

function hasSkipped(descriptor, relativePath, reason) {
  return descriptor.skipped.some((item) => item.relative_path === relativePath && item.reason === reason);
}

test("normalizeTransport recognizes stdio and remote MCP transports", () => {
  assert.equal(normalizeTransport({ command: "node" }), "stdio");
  assert.equal(normalizeTransport({ url: "https://mcp.example/mcp" }), "streamable_http");
  assert.equal(normalizeTransport({ transport: "streamable-http" }), "streamable_http");
  assert.equal(normalizeTransport({ transport: "sse" }), "sse");
  assert.equal(normalizeTransport({}), "unknown");
});

test("scanMcpServerDescriptor recovers local stdio server source from node script args", () => {
  const root = makeTempRoot();
  const serverRoot = path.join(root, "server");
  writeJson(path.join(serverRoot, "package.json"), {
    name: "demo-mcp",
    type: "module",
  });
  writeFile(path.join(serverRoot, "src", "server.js"), [
    "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';",
    "import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';",
    "const server = new McpServer({ name: 'demo', version: '1.0.0' });",
    "server.tool('read_file', 'Read local files', {}, async () => ({}));",
    "await server.connect(new StdioServerTransport());",
  ].join("\n"));
  writeFile(path.join(serverRoot, "README.md"), "# Demo MCP\n");

  const descriptor = scanMcpServerDescriptor("demo", {
    command: "node",
    args: ["./src/server.js"],
    cwd: serverRoot,
    tools: [
      {
        name: "read_file",
        description: "Read local files",
        inputSchema: { type: "object" },
      },
    ],
  });
  const files = byPath(descriptor.files);

  assert.equal(descriptor.object_type, "mcp");
  assert.equal(descriptor.source_framework, "mcp_native");
  assert.equal(descriptor.name, "demo");
  assert.equal(descriptor.transport, "stdio");
  assert.equal(descriptor.remote, false);
  assert.equal(descriptor.source_status, "source_recovered");
  assert.equal(descriptor.extraction.level, "source_directory");
  assert.equal(descriptor.extraction.confidence, "high");
  assert.equal(descriptor.extraction.sdk_detected, true);
  assert.equal(descriptor.extraction.hookable, true);
  assert.equal(descriptor.tool_count, 1);
  assert.equal(descriptor.tools[0].name, "read_file");
  assert.equal(files["package.json"].kind, "manifest");
  assert.equal(files["src/server.js"].kind, "script");
  assert.match(files["src/server.js"].content, /McpServer/);
  assert.equal(descriptor.sdk.packages.includes("@modelcontextprotocol/sdk"), true);
  assert.equal(typeof descriptor.sha256, "string");
  assert.equal(descriptor.sha256.length, 64);
});

test("scanMcpServerDescriptor recovers local Python MCP server and detects FastMCP", () => {
  const root = makeTempRoot();
  const serverRoot = path.join(root, "py-server");
  writeFile(path.join(serverRoot, "pyproject.toml"), "[project]\nname = 'py-mcp'\n");
  writeFile(path.join(serverRoot, "server.py"), [
    "from mcp.server.fastmcp import FastMCP",
    "mcp = FastMCP('demo')",
    "@mcp.tool()",
    "def search(query: str) -> str:",
    "    return query",
  ].join("\n"));

  const descriptor = scanMcpServerDescriptor("py-demo", {
    command: "python",
    args: ["server.py"],
    cwd: serverRoot,
  });
  const files = byPath(descriptor.files);

  assert.equal(descriptor.source_status, "source_recovered");
  assert.equal(descriptor.extraction.sdk_detected, true);
  assert.equal(descriptor.sdk.packages.includes("mcp"), true);
  assert.equal(files["pyproject.toml"].kind, "manifest");
  assert.equal(files["server.py"].kind, "script");
  assert.match(files["server.py"].content, /FastMCP/);
});

test("scanMcpServerDescriptor records package runner MCP without claiming source recovery", () => {
  const root = makeTempRoot();
  const descriptor = scanMcpServerDescriptor("package-demo", {
    command: "npx",
    args: ["-y", "@example/mcp-server"],
    cwd: root,
  });

  assert.equal(descriptor.transport, "stdio");
  assert.equal(descriptor.remote, false);
  assert.equal(descriptor.source_status, "package_reference_only");
  assert.equal(descriptor.extraction.level, "config");
  assert.equal(descriptor.extraction.confidence, "low");
  assert.match(descriptor.source_reason, /package runner/);
  assert.deepEqual(descriptor.files, []);
});

test("scanMcpServerDescriptor records remote HTTP MCP metadata and redacts secrets", () => {
  const descriptor = scanMcpServerDescriptor("remote-demo", {
    transport: "streamable-http",
    url: "https://mcp.example/mcp",
    headers: {
      Authorization: "Bearer secret",
      "X-Workspace": "abc",
    },
    tools: [
      {
        name: "web_search",
        description: "Search the web",
        input_schema: { type: "object", properties: { q: { type: "string" } } },
      },
    ],
  });

  assert.equal(descriptor.transport, "streamable_http");
  assert.equal(descriptor.remote, true);
  assert.equal(descriptor.source_status, "remote_source_unavailable");
  assert.equal(descriptor.extraction.level, "remote_metadata");
  assert.equal(descriptor.url, "https://mcp.example/mcp");
  assert.deepEqual(descriptor.server_config.header_keys, ["Authorization", "X-Workspace"]);
  assert.equal(descriptor.server_config.headers.Authorization, "[redacted]");
  assert.equal(descriptor.tools[0].input_schema.properties.q.type, "string");
  assert.deepEqual(descriptor.files, []);
});

test("scanMcpConfigFile extracts multiple mcpServers from config", () => {
  const root = makeTempRoot();
  const localRoot = path.join(root, "local-server");
  writeJson(path.join(localRoot, "package.json"), { name: "local-mcp" });
  writeFile(path.join(localRoot, "server.js"), "console.log('mcp');\n");
  const configPath = path.join(root, ".cursor", "mcp.json");
  writeJson(configPath, {
    mcpServers: {
      local: {
        command: "node",
        args: ["server.js"],
        cwd: localRoot,
        env: {
          API_KEY: "secret",
        },
      },
      remote: {
        url: "https://remote.example/mcp",
      },
    },
  });

  const result = scanMcpConfigFile(configPath);
  const byName = Object.fromEntries(result.servers.map((server) => [server.name, server]));

  assert.equal(result.diagnostics.length, 0);
  assert.equal(result.servers.length, 2);
  assert.equal(byName.local.source_status, "source_recovered");
  assert.equal(byName.local.config_key, "mcpServers");
  assert.deepEqual(byName.local.server_config.env_keys, ["API_KEY"]);
  assert.equal(byName.local.server_config.env.API_KEY, "[redacted]");
  assert.equal(byName.remote.remote, true);
  assert.equal(byName.remote.source_status, "remote_source_unavailable");
});

test("scanMcpConfigFile reports configs without MCP servers", () => {
  const root = makeTempRoot();
  const configPath = path.join(root, "mcp.json");
  writeJson(configPath, { notMcpServers: {} });

  const result = scanMcpConfigFile(configPath);

  assert.deepEqual(result.servers, []);
  assert.equal(result.diagnostics[0].reason, "no_mcp_servers");
});

test("findMcpConfigFiles discovers common MCP config names under roots", () => {
  const root = makeTempRoot();
  writeJson(path.join(root, ".cursor", "mcp.json"), { mcpServers: {} });
  writeJson(path.join(root, "claude_desktop_config.json"), { mcpServers: {} });
  writeJson(path.join(root, "nested", "mcp.json"), { mcpServers: {} });

  const diagnostics = [];
  const found = findMcpConfigFiles({
    roots: [root],
  }, diagnostics);
  const rel = found.map((item) => path.relative(root, item).split(path.sep).join("/")).sort();

  assert.deepEqual(rel, [".cursor/mcp.json", "claude_desktop_config.json"]);
  assert.deepEqual(diagnostics, []);
});

test("scanMcpConfigs scans explicit config paths and root-discovered configs", () => {
  const root = makeTempRoot();
  const configA = path.join(root, "mcp.json");
  const configB = path.join(root, "custom.json");
  writeJson(configA, {
    mcpServers: {
      a: { url: "https://a.example/mcp" },
    },
  });
  writeJson(configB, {
    servers: {
      b: { url: "https://b.example/mcp" },
    },
  });

  const result = scanMcpConfigs({
    roots: [root],
    configPaths: [configB],
  });
  const names = result.mcps.map((server) => server.name).sort();

  assert.deepEqual(names, ["a", "b"]);
  assert.equal(result.summary.mcp_count, 2);
  assert.equal(result.summary.config_paths.length, 2);
});

test("scanMcpSourceDirectory records binary files without content", () => {
  const root = makeTempRoot();
  writeJson(path.join(root, "package.json"), { name: "binary-mcp" });
  writeFile(path.join(root, "server.js"), "console.log('ok');\n");
  writeFile(path.join(root, "assets", "icon.png"), Buffer.from([0x89, 0x50, 0x00, 0x01]));

  const result = scanMcpSourceDirectory(root);
  const files = byPath(result.files);

  assert.equal(files["assets/icon.png"].kind, "asset");
  assert.equal(files["assets/icon.png"].binary, true);
  assert.equal(files["assets/icon.png"].content_omitted, true);
  assert.equal(files["assets/icon.png"].reason, "binary");
  assert.equal(Object.prototype.hasOwnProperty.call(files["assets/icon.png"], "content"), false);
});

test("scanMcpSourceDirectory omits large files and respects total text byte limits", () => {
  const root = makeTempRoot();
  writeJson(path.join(root, "package.json"), { name: "limit-mcp" });
  writeFile(path.join(root, "server.js"), "x".repeat(64));
  writeFile(path.join(root, "a.md"), "a".repeat(20));
  writeFile(path.join(root, "b.md"), "b".repeat(20));

  const result = scanMcpSourceDirectory(root, {
    maxFileBytes: 40,
    maxTotalBytesPerServer: 50,
  });
  const files = byPath(result.files);

  assert.equal(files["server.js"].content_omitted, true);
  assert.equal(files["server.js"].reason, "too_large");
  assert.equal(files["a.md"].content, "a".repeat(20));
  assert.equal(files["b.md"].content_omitted, true);
  assert.equal(files["b.md"].reason, "max_total_bytes_exceeded");
});

test("scanMcpSourceDirectory excludes noisy directories and files", () => {
  const root = makeTempRoot();
  writeJson(path.join(root, "package.json"), { name: "exclude-mcp" });
  writeFile(path.join(root, "server.js"), "console.log('ok');\n");
  writeFile(path.join(root, "node_modules", "pkg", "index.js"), "module.exports = {};");
  writeFile(path.join(root, ".git", "config"), "[core]\n");
  writeFile(path.join(root, ".env"), "SECRET=1\n");

  const result = scanMcpSourceDirectory(root);
  const files = byPath(result.files);

  assert.equal(Boolean(files["server.js"]), true);
  assert.equal(Boolean(files["node_modules/pkg/index.js"]), false);
  assert.equal(Boolean(files[".git/config"]), false);
  assert.equal(Boolean(files[".env"]), false);
  assert.equal(result.skipped.some((item) => item.relative_path === "node_modules" && item.reason === "excluded_directory"), true);
  assert.equal(result.skipped.some((item) => item.relative_path === ".git" && item.reason === "excluded_directory"), true);
  assert.equal(result.skipped.some((item) => item.relative_path === ".env" && item.reason === "excluded_file"), true);
});

test("scanMcpServerDescriptor skips symlinks by default", { skip: process.platform === "win32" ? false : undefined }, () => {
  const root = makeTempRoot();
  const serverRoot = path.join(root, "server");
  writeJson(path.join(serverRoot, "package.json"), { name: "symlink-mcp" });
  writeFile(path.join(serverRoot, "server.js"), "console.log('ok');\n");
  writeFile(path.join(root, "outside.txt"), "outside");
  const linkPath = path.join(serverRoot, "outside-link.txt");
  try {
    fs.symlinkSync(path.join(root, "outside.txt"), linkPath, "file");
  } catch (error) {
    if (process.platform === "win32") {
      return;
    }
    throw error;
  }

  const descriptor = scanMcpServerDescriptor("symlink-demo", {
    command: "node",
    args: ["server.js"],
    cwd: serverRoot,
  });
  const files = byPath(descriptor.files);

  assert.equal(Boolean(files["outside-link.txt"]), false);
  assert.equal(hasSkipped(descriptor, "outside-link.txt", "symlink"), true);
});

test("scanMcpSourceDirectory enforces maxFilesPerServer and keeps deterministic order", () => {
  const root = makeTempRoot();
  writeJson(path.join(root, "package.json"), { name: "many-mcp" });
  writeFile(path.join(root, "a.js"), "a");
  writeFile(path.join(root, "b.js"), "b");
  writeFile(path.join(root, "c.js"), "c");

  const result = scanMcpSourceDirectory(root, {
    maxFilesPerServer: 3,
  });

  assert.deepEqual(result.files.map((item) => item.relative_path), ["package.json", "a.js", "b.js"]);
  assert.equal(result.skipped.some((item) => item.relative_path === "c.js" && item.reason === "max_files_exceeded"), true);
});

test("detectMcpSdkUsage returns evidence for TypeScript and Python MCP SDKs", () => {
  const result = detectMcpSdkUsage([
    {
      relative_path: "server.ts",
      content: "import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';",
    },
    {
      relative_path: "server.py",
      content: "from mcp.server.fastmcp import FastMCP\nmcp = FastMCP('x')",
    },
  ]);

  assert.equal(result.detected, true);
  assert.deepEqual(result.packages, ["@modelcontextprotocol/sdk", "mcp"]);
  assert.equal(result.evidence.length, 2);
});

test("classifyMcpFile identifies MCP source roles", () => {
  assert.equal(classifyMcpFile("mcp.json"), "mcp_config");
  assert.equal(classifyMcpFile(".cursor/mcp.json"), "mcp_config");
  assert.equal(classifyMcpFile("package.json"), "manifest");
  assert.equal(classifyMcpFile("pyproject.toml"), "manifest");
  assert.equal(classifyMcpFile("input_schema.json"), "schema");
  assert.equal(classifyMcpFile("server.ts"), "script");
  assert.equal(classifyMcpFile("README.md"), "text");
  assert.equal(classifyMcpFile("assets/logo.png"), "asset");
});

test("descriptor hashes change when source files change", () => {
  const root = makeTempRoot();
  writeJson(path.join(root, "package.json"), { name: "hash-mcp" });
  const serverPath = path.join(root, "server.js");
  writeFile(serverPath, "console.log('one');\n");
  const first = scanMcpServerDescriptor("hash-demo", {
    command: "node",
    args: ["server.js"],
    cwd: root,
  });

  writeFile(serverPath, "console.log('two');\n");
  const second = scanMcpServerDescriptor("hash-demo", {
    command: "node",
    args: ["server.js"],
    cwd: root,
  });

  assert.notEqual(first.sha256, second.sha256);
  assert.equal(
    byPath(second.files)["server.js"].sha256,
    crypto.createHash("sha256").update(fs.readFileSync(serverPath)).digest("hex"),
  );
});

test("resolveScanPath handles config-relative paths and home expansion", () => {
  const base = makeTempRoot();
  assert.equal(resolveScanPath("./mcp.json", base), path.join(base, "mcp.json"));
  assert.equal(resolveScanPath("~/mcp.json", base), path.join(os.homedir(), "mcp.json"));
});
