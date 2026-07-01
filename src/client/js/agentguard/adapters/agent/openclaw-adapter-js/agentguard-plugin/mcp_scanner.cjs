"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const DEFAULT_CONFIG_NAMES = [
  "mcp.json",
  ".mcp.json",
  ".cursor/mcp.json",
  ".vscode/mcp.json",
  "claude_desktop_config.json",
];

const DEFAULT_EXCLUDE_DIRS = [
  ".git",
  ".hg",
  ".svn",
  ".cache",
  ".venv",
  "__pycache__",
  "build",
  "coverage",
  "dist",
  "node_modules",
];

const DEFAULT_EXCLUDE_FILES = [
  ".DS_Store",
  "Thumbs.db",
  ".env",
  ".env.local",
];

const DEFAULT_TEXT_EXTENSIONS = new Set([
  ".cjs",
  ".css",
  ".csv",
  ".html",
  ".ini",
  ".js",
  ".json",
  ".jsonl",
  ".jsx",
  ".md",
  ".mjs",
  ".mts",
  ".py",
  ".sh",
  ".toml",
  ".ts",
  ".tsx",
  ".txt",
  ".xml",
  ".yaml",
  ".yml",
]);

const LOCAL_COMMANDS = new Set([
  "bun",
  "deno",
  "node",
  "python",
  "python3",
  "py",
  "tsx",
  "uv",
]);

const PACKAGE_COMMANDS = new Set([
  "npx",
  "pnpm",
  "yarn",
  "uvx",
  "pipx",
]);

const SCRIPT_EXTENSIONS = new Set([
  ".cjs",
  ".js",
  ".mjs",
  ".mts",
  ".py",
  ".sh",
  ".ts",
  ".tsx",
]);

const DEFAULT_OPTIONS = Object.freeze({
  roots: [],
  configPaths: [],
  configNames: DEFAULT_CONFIG_NAMES,
  baseDir: process.cwd(),
  maxFileBytes: 200_000,
  maxTotalBytesPerServer: 2_000_000,
  maxFilesPerServer: 200,
  excludeDirs: DEFAULT_EXCLUDE_DIRS,
  excludeFiles: DEFAULT_EXCLUDE_FILES,
  textExtensions: [...DEFAULT_TEXT_EXTENSIONS],
  followSymlinks: false,
});

function scanMcpConfigs(options = {}) {
  const resolved = normalizeOptions(options);
  const diagnostics = [];
  const configFiles = findMcpConfigFiles(resolved, diagnostics);
  const servers = [];

  for (const configPath of configFiles) {
    try {
      const result = scanMcpConfigFile(configPath, resolved);
      servers.push(...result.servers);
      diagnostics.push(...result.diagnostics);
    } catch (error) {
      diagnostics.push({
        level: "error",
        path: configPath,
        reason: "config_scan_failed",
        message: String(error && error.message ? error.message : error),
      });
    }
  }

  return {
    mcps: servers,
    diagnostics,
    summary: {
      roots: resolved.roots,
      config_paths: configFiles,
      mcp_count: servers.length,
      diagnostic_count: diagnostics.length,
    },
  };
}

function findMcpConfigFiles(options = {}, diagnostics = []) {
  const resolved = normalizeOptions(options);
  const out = [];
  const seen = new Set();

  for (const configPath of resolved.configPaths) {
    addConfigCandidate(configPath, out, seen, diagnostics);
  }

  for (const root of resolved.roots) {
    let stat;
    try {
      stat = fs.lstatSync(root);
    } catch (error) {
      diagnostics.push({
        level: "warning",
        path: root,
        reason: "root_unreadable",
        message: String(error && error.message ? error.message : error),
      });
      continue;
    }
    if (stat.isSymbolicLink() && !resolved.followSymlinks) {
      diagnostics.push({
        level: "warning",
        path: root,
        reason: "root_symlink_skipped",
      });
      continue;
    }
    if (stat.isFile()) {
      addConfigCandidate(root, out, seen, diagnostics);
      continue;
    }
    if (!stat.isDirectory()) {
      diagnostics.push({
        level: "warning",
        path: root,
        reason: "root_not_file_or_directory",
      });
      continue;
    }
    for (const configName of resolved.configNames) {
      addConfigCandidate(path.join(root, configName), out, seen, diagnostics, {
        missingIsDiagnostic: false,
      });
    }
  }

  return out.sort(comparePaths);
}

function scanMcpConfigFile(configPath, options = {}) {
  const resolved = normalizeOptions(options);
  const realConfigPath = fs.realpathSync(resolveScanPath(configPath, resolved.baseDir));
  const configDir = inferConfigBaseDir(realConfigPath);
  const source = fs.readFileSync(realConfigPath, "utf8");
  const parsed = parseJson(source, realConfigPath);
  const extracted = extractServerMap(parsed);
  const diagnostics = [];
  const servers = [];

  if (!extracted.servers || Object.keys(extracted.servers).length === 0) {
    diagnostics.push({
      level: "warning",
      path: realConfigPath,
      reason: "no_mcp_servers",
      message: "MCP config did not contain mcpServers or servers entries.",
    });
    return { servers, diagnostics };
  }

  for (const [serverName, serverConfig] of Object.entries(extracted.servers)) {
    if (!serverConfig || typeof serverConfig !== "object" || Array.isArray(serverConfig)) {
      diagnostics.push({
        level: "warning",
        path: realConfigPath,
        server: serverName,
        reason: "invalid_server_config",
      });
      continue;
    }
    try {
      servers.push(scanMcpServerDescriptor(serverName, serverConfig, {
        ...resolved,
        configPath: realConfigPath,
        configDir,
        configKey: extracted.key,
      }));
    } catch (error) {
      diagnostics.push({
        level: "error",
        path: realConfigPath,
        server: serverName,
        reason: "server_scan_failed",
        message: String(error && error.message ? error.message : error),
      });
    }
  }

  return { servers, diagnostics };
}

function scanMcpServerDescriptor(serverName, serverConfig, options = {}) {
  const resolved = normalizeOptions(options);
  const configDir = resolveScanPath(options.configDir || resolved.baseDir, resolved.baseDir);
  const configPath = options.configPath ? resolveScanPath(options.configPath, resolved.baseDir) : "";
  const name = nonEmptyString(serverConfig.name) || String(serverName || "mcp_server");
  const description = nonEmptyString(serverConfig.description)
    || nonEmptyString(serverConfig.title)
    || "";
  const transport = normalizeTransport(serverConfig);
  const remote = isRemoteServer(serverConfig, transport);
  const sanitizedConfig = sanitizeServerConfig(serverConfig, configDir);
  const toolDescriptors = normalizeTools(serverConfig.tools);
  const source = remote
    ? remoteSourceMetadata(serverConfig, transport)
    : inferLocalSource(serverConfig, configDir);
  const scan = source.source_root
    ? scanMcpSourceDirectory(source.source_root, resolved)
    : emptySourceScan();
  const sdk = detectMcpSdkUsage(scan.files);
  const sha256 = hashMcpDescriptor({
    name,
    configPath,
    sanitizedConfig,
    files: scan.files,
    source,
  });

  return {
    object_type: "mcp",
    source_framework: "mcp_native",
    name,
    description,
    transport,
    remote,
    config_path: configPath,
    config_key: nonEmptyString(options.configKey) || "",
    server_config: sanitizedConfig,
    command: nonEmptyString(serverConfig.command) || "",
    args: Array.isArray(serverConfig.args) ? serverConfig.args.map(String) : [],
    cwd: source.cwd || "",
    url: nonEmptyString(serverConfig.url) || nonEmptyString(serverConfig.endpoint) || "",
    entry_file: source.entry_file || "",
    root_path: source.source_root || "",
    source_status: source.status,
    source_reason: source.reason || "",
    sha256,
    tools: toolDescriptors,
    tool_count: toolDescriptors.length,
    files: scan.files,
    skipped: scan.skipped,
    file_count: scan.files.length,
    total_size: scan.files.reduce((sum, item) => sum + Number(item.size || 0), 0),
    sdk,
    extraction: {
      level: extractionLevelForSource(source),
      confidence: source.confidence,
      source_status: source.status,
      source_reason: source.reason || "",
      truncated: scan.files.some((item) => item.content_omitted),
      sdk_detected: sdk.detected,
      hookable: sdk.detected && source.status === "source_recovered",
    },
  };
}

function scanMcpSourceDirectory(sourceRoot, options = {}) {
  const resolved = normalizeOptions(options);
  const rootPath = resolveScanPath(sourceRoot, resolved.baseDir);
  const realRoot = fs.realpathSync(rootPath);
  const state = {
    files: [],
    skipped: [],
    textBytesIncluded: 0,
    filesIncluded: 0,
  };
  collectMcpFiles(realRoot, realRoot, resolved, state);
  state.files.sort(compareFileRecords);
  state.skipped.sort((a, b) => String(a.relative_path || a.path).localeCompare(String(b.relative_path || b.path)));
  return state;
}

function emptySourceScan() {
  return {
    files: [],
    skipped: [],
  };
}

function inferLocalSource(serverConfig, configDir) {
  const command = nonEmptyString(serverConfig.command);
  const args = Array.isArray(serverConfig.args) ? serverConfig.args.map(String) : [];
  const cwd = resolveOptionalPath(serverConfig.cwd, configDir) || configDir;
  const commandBase = command ? path.basename(command).replace(/\.(cmd|exe|ps1|bat)$/i, "").toLowerCase() : "";
  const localCommandPath = resolveLocalPath(command, cwd);
  const entryFromCommand = localCommandPath && isRegularReadableFile(localCommandPath)
    ? localCommandPath
    : "";
  const entryFromArgs = findLocalScriptArg(args, cwd, commandBase);
  const entryFile = entryFromCommand || entryFromArgs;

  if (entryFile) {
    const sourceRoot = findProjectRoot(path.dirname(entryFile), cwd);
    return {
      status: "source_recovered",
      confidence: "high",
      cwd,
      entry_file: normalizePath(entryFile),
      source_root: normalizePath(sourceRoot),
    };
  }

  if (command && PACKAGE_COMMANDS.has(commandBase)) {
    return {
      status: "package_reference_only",
      confidence: "low",
      cwd,
      source_root: "",
      entry_file: "",
      reason: "MCP server is launched through a package runner; local package source was not resolved.",
    };
  }

  if (command && LOCAL_COMMANDS.has(commandBase) && isDirectory(cwd)) {
    const manifestRoot = findManifestRoot(cwd);
    if (manifestRoot) {
      return {
        status: "source_recovered",
        confidence: "medium",
        cwd,
        entry_file: "",
        source_root: normalizePath(manifestRoot),
        reason: "No explicit entry script was found; using the local MCP server working directory.",
      };
    }
  }

  if (isDirectory(cwd) && hasProjectManifest(cwd)) {
    return {
      status: "source_recovered",
      confidence: "medium",
      cwd,
      entry_file: "",
      source_root: normalizePath(cwd),
      reason: "Using MCP server working directory because it contains a project manifest.",
    };
  }

  return {
    status: "source_unresolved",
    confidence: "low",
    cwd,
    source_root: "",
    entry_file: "",
    reason: "No local MCP server source root could be inferred from command, args, or cwd.",
  };
}

function remoteSourceMetadata(serverConfig, transport) {
  return {
    status: "remote_source_unavailable",
    confidence: "high",
    cwd: "",
    source_root: "",
    entry_file: "",
    reason: `MCP ${transport} server is remote; local adapter cannot recover server source code.`,
  };
}

function collectMcpFiles(rootPath, currentPath, options, state) {
  let entries;
  try {
    entries = fs.readdirSync(currentPath, { withFileTypes: true });
  } catch (error) {
    state.skipped.push({
      relative_path: toRelativePath(rootPath, currentPath),
      reason: "directory_unreadable",
      message: String(error && error.message ? error.message : error),
    });
    return;
  }

  for (const entry of sortDirents(entries)) {
    const fullPath = path.join(currentPath, entry.name);
    const relativePath = toRelativePath(rootPath, fullPath);

    if (entry.isSymbolicLink() && !options.followSymlinks) {
      state.skipped.push({
        relative_path: relativePath,
        reason: "symlink",
      });
      continue;
    }

    if (entry.isDirectory()) {
      if (options.excludeDirs.has(entry.name)) {
        state.skipped.push({
          relative_path: relativePath,
          reason: "excluded_directory",
        });
        continue;
      }
      const realDir = safeRealpath(fullPath);
      if (!isPathInside(rootPath, realDir)) {
        state.skipped.push({
          relative_path: relativePath,
          reason: "outside_source_root",
        });
        continue;
      }
      collectMcpFiles(rootPath, fullPath, options, state);
      continue;
    }

    if (!entry.isFile()) {
      state.skipped.push({
        relative_path: relativePath,
        reason: "not_regular_file",
      });
      continue;
    }
    if (options.excludeFiles.has(entry.name)) {
      state.skipped.push({
        relative_path: relativePath,
        reason: "excluded_file",
      });
      continue;
    }
    if (state.filesIncluded >= options.maxFilesPerServer) {
      state.skipped.push({
        relative_path: relativePath,
        reason: "max_files_exceeded",
      });
      continue;
    }

    state.files.push(readMcpFile(rootPath, fullPath, options, state));
    state.filesIncluded += 1;
  }
}

function readMcpFile(rootPath, filePath, options, state) {
  const relativePath = toRelativePath(rootPath, filePath);
  const stat = fs.statSync(filePath);
  const sha256 = hashFile(filePath);
  const base = {
    relative_path: relativePath,
    kind: classifyMcpFile(relativePath),
    size: stat.size,
    sha256,
  };

  if (stat.size > options.maxFileBytes) {
    return {
      ...base,
      content_omitted: true,
      reason: "too_large",
    };
  }
  if ((state.textBytesIncluded + stat.size) > options.maxTotalBytesPerServer) {
    return {
      ...base,
      content_omitted: true,
      reason: "max_total_bytes_exceeded",
    };
  }

  const buffer = fs.readFileSync(filePath);
  const binary = isBinaryBuffer(buffer, relativePath, options);
  if (binary) {
    return {
      ...base,
      binary: true,
      content_omitted: true,
      reason: "binary",
    };
  }

  const content = buffer.toString("utf8");
  state.textBytesIncluded += buffer.length;
  const out = {
    ...base,
    binary: false,
    content,
  };
  if (base.kind === "manifest" || relativePath.toLowerCase().endsWith(".json")) {
    const parsed = parseJsonSafe(content);
    if (parsed.ok) {
      out.parsed_json = parsed.value;
    } else {
      out.parse_error = parsed.error;
    }
  }
  return out;
}

function classifyMcpFile(relativePath) {
  const normalized = normalizeRelativePath(relativePath).toLowerCase();
  const name = path.posix.basename(normalized);
  const ext = path.posix.extname(normalized);
  if (DEFAULT_CONFIG_NAMES.includes(normalized) || name === "mcp.json") {
    return "mcp_config";
  }
  if (["package.json", "pyproject.toml", "uv.lock", "requirements.txt"].includes(name)) {
    return "manifest";
  }
  if (name.includes("schema") && [".json", ".yaml", ".yml", ".sql"].includes(ext)) {
    return "schema";
  }
  if (SCRIPT_EXTENSIONS.has(ext)) {
    return "script";
  }
  if ([".md", ".txt"].includes(ext)) {
    return "text";
  }
  return "asset";
}

function normalizeTransport(serverConfig) {
  const explicit = nonEmptyString(serverConfig.transport)
    || nonEmptyString(serverConfig.type)
    || nonEmptyString(serverConfig.transportType);
  const normalized = explicit.toLowerCase().replace(/[-\s]/g, "_");
  if (normalized === "streamablehttp" || normalized === "streamable_http") {
    return "streamable_http";
  }
  if (normalized === "http" || normalized === "https") {
    return "streamable_http";
  }
  if (normalized === "sse") {
    return "sse";
  }
  if (normalized === "stdio") {
    return "stdio";
  }
  if (serverConfig.url || serverConfig.endpoint) {
    return "streamable_http";
  }
  if (serverConfig.command) {
    return "stdio";
  }
  return "unknown";
}

function isRemoteServer(serverConfig, transport) {
  const url = nonEmptyString(serverConfig.url) || nonEmptyString(serverConfig.endpoint);
  if (url && /^https?:\/\//i.test(url)) {
    return true;
  }
  return transport === "streamable_http" || transport === "sse";
}

function normalizeTools(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .filter((item) => item && typeof item === "object" && !Array.isArray(item))
    .map((tool, index) => ({
      name: nonEmptyString(tool.name) || `tool_${index}`,
      description: nonEmptyString(tool.description) || "",
      input_schema: tool.input_schema || tool.inputSchema || tool.schema || null,
    }));
}

function extractServerMap(parsed) {
  if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
    if (parsed.mcpServers && typeof parsed.mcpServers === "object" && !Array.isArray(parsed.mcpServers)) {
      return { key: "mcpServers", servers: parsed.mcpServers };
    }
    if (parsed.servers && typeof parsed.servers === "object" && !Array.isArray(parsed.servers)) {
      return { key: "servers", servers: parsed.servers };
    }
    if (
      parsed.mcp
      && typeof parsed.mcp === "object"
      && !Array.isArray(parsed.mcp)
      && parsed.mcp.servers
      && typeof parsed.mcp.servers === "object"
      && !Array.isArray(parsed.mcp.servers)
    ) {
      return { key: "mcp.servers", servers: parsed.mcp.servers };
    }
  }
  return { key: "", servers: {} };
}

function sanitizeServerConfig(serverConfig, configDir) {
  const out = {};
  for (const key of [
    "name",
    "description",
    "title",
    "transport",
    "transportType",
    "type",
    "command",
    "args",
    "cwd",
    "url",
    "endpoint",
  ]) {
    if (serverConfig[key] !== undefined) {
      out[key] = key === "cwd"
        ? resolveOptionalPath(serverConfig[key], configDir) || String(serverConfig[key] || "")
        : cloneJsonSafe(serverConfig[key]);
    }
  }
  if (serverConfig.env && typeof serverConfig.env === "object" && !Array.isArray(serverConfig.env)) {
    out.env_keys = Object.keys(serverConfig.env).sort();
    out.env = Object.fromEntries(out.env_keys.map((key) => [key, "[redacted]"]));
  }
  if (serverConfig.headers && typeof serverConfig.headers === "object" && !Array.isArray(serverConfig.headers)) {
    out.header_keys = Object.keys(serverConfig.headers).sort();
    out.headers = Object.fromEntries(out.header_keys.map((key) => [key, "[redacted]"]));
  }
  if (Array.isArray(serverConfig.tools)) {
    out.tools = normalizeTools(serverConfig.tools);
  }
  return out;
}

function inferConfigBaseDir(configPath) {
  const configDir = path.dirname(configPath);
  const parentName = path.basename(configDir).toLowerCase();
  if (parentName === ".cursor" || parentName === ".vscode") {
    return path.dirname(configDir);
  }
  return configDir;
}

function findLocalScriptArg(args, cwd, commandBase) {
  const candidates = [];
  for (let index = 0; index < args.length; index += 1) {
    const arg = args[index];
    if (!arg || arg.startsWith("-")) {
      continue;
    }
    if (commandBase === "uv" && ["run", "tool"].includes(arg)) {
      continue;
    }
    if (commandBase === "deno" && ["run", "serve"].includes(arg)) {
      continue;
    }
    if (commandBase === "bun" && ["run"].includes(arg)) {
      continue;
    }
    const localPath = resolveLocalPath(arg, cwd);
    if (!localPath) {
      continue;
    }
    const ext = path.extname(localPath).toLowerCase();
    if (SCRIPT_EXTENSIONS.has(ext) && isRegularReadableFile(localPath)) {
      candidates.push(localPath);
    }
  }
  return candidates[0] || "";
}

function findProjectRoot(entryDir, fallbackDir) {
  const root = findManifestRoot(entryDir);
  if (root) {
    return root;
  }
  return fallbackDir && isPathInside(fallbackDir, entryDir) ? fallbackDir : entryDir;
}

function findManifestRoot(startDir) {
  let current = safeRealpath(startDir);
  while (current) {
    if (hasProjectManifest(current)) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      break;
    }
    current = parent;
  }
  return "";
}

function hasProjectManifest(dirPath) {
  return [
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
  ].some((name) => isRegularReadableFile(path.join(dirPath, name)));
}

function detectMcpSdkUsage(files) {
  const evidence = [];
  const packages = new Set();
  for (const file of files || []) {
    if (typeof file.content !== "string") {
      continue;
    }
    const content = file.content;
    const matched = [];
    if (content.includes("@modelcontextprotocol/sdk")) {
      matched.push("@modelcontextprotocol/sdk");
      packages.add("@modelcontextprotocol/sdk");
    }
    if (/\bfrom\s+mcp\b|\bimport\s+mcp\b/.test(content) || content.includes("from mcp.server")) {
      matched.push("python-mcp-sdk");
      packages.add("mcp");
    }
    for (const symbol of ["McpServer", "FastMCP", "StdioServerTransport", "StreamableHTTPServerTransport"]) {
      if (content.includes(symbol)) {
        matched.push(symbol);
      }
    }
    if (matched.length) {
      evidence.push({
        relative_path: file.relative_path,
        matches: [...new Set(matched)],
      });
    }
  }
  return {
    detected: evidence.length > 0,
    packages: [...packages].sort(),
    evidence: evidence.slice(0, 20),
  };
}

function extractionLevelForSource(source) {
  if (source.status === "source_recovered") {
    return "source_directory";
  }
  if (source.status === "remote_source_unavailable") {
    return "remote_metadata";
  }
  return "config";
}

function hashMcpDescriptor({ name, configPath, sanitizedConfig, files, source }) {
  const hash = crypto.createHash("sha256");
  hash.update(String(name || ""));
  hash.update("\0");
  hash.update(String(configPath || ""));
  hash.update("\0");
  hash.update(JSON.stringify(sanitizedConfig || {}, Object.keys(sanitizedConfig || {}).sort()));
  hash.update("\0");
  hash.update(String(source && source.status || ""));
  hash.update("\0");
  for (const file of [...(files || [])].sort((a, b) => a.relative_path.localeCompare(b.relative_path))) {
    hash.update(file.relative_path);
    hash.update("\0");
    hash.update(file.sha256 || "");
    hash.update("\0");
  }
  return hash.digest("hex");
}

function addConfigCandidate(filePath, out, seen, diagnostics, options = {}) {
  const missingIsDiagnostic = options.missingIsDiagnostic !== false;
  const resolved = resolveScanPath(filePath, process.cwd());
  let stat;
  try {
    stat = fs.lstatSync(resolved);
  } catch (error) {
    if (missingIsDiagnostic) {
      diagnostics.push({
        level: "warning",
        path: resolved,
        reason: "config_unreadable",
        message: String(error && error.message ? error.message : error),
      });
    }
    return;
  }
  if (!stat.isFile()) {
    if (missingIsDiagnostic) {
      diagnostics.push({
        level: "warning",
        path: resolved,
        reason: "config_not_file",
      });
    }
    return;
  }
  const real = fs.realpathSync(resolved);
  if (seen.has(real)) {
    return;
  }
  seen.add(real);
  out.push(real);
}

function parseJson(source, filePath) {
  try {
    return JSON.parse(source);
  } catch (error) {
    error.message = `Failed to parse MCP config at ${filePath}: ${error.message}`;
    throw error;
  }
}

function parseJsonSafe(content) {
  try {
    return { ok: true, value: JSON.parse(content) };
  } catch (error) {
    return {
      ok: false,
      error: String(error && error.message ? error.message : error),
    };
  }
}

function normalizeOptions(options = {}) {
  const input = {
    ...DEFAULT_OPTIONS,
    ...options,
  };
  const baseDir = resolveScanPath(input.baseDir || process.cwd(), process.cwd());
  return {
    ...input,
    baseDir,
    roots: (input.roots || []).map((item) => resolveScanPath(item, baseDir)),
    configPaths: (input.configPaths || []).map((item) => resolveScanPath(item, baseDir)),
    configNames: normalizeStringArray(input.configNames, DEFAULT_CONFIG_NAMES),
    maxFileBytes: positiveInteger(input.maxFileBytes, DEFAULT_OPTIONS.maxFileBytes),
    maxTotalBytesPerServer: positiveInteger(
      input.maxTotalBytesPerServer,
      DEFAULT_OPTIONS.maxTotalBytesPerServer,
    ),
    maxFilesPerServer: positiveInteger(input.maxFilesPerServer, DEFAULT_OPTIONS.maxFilesPerServer),
    excludeDirs: new Set(input.excludeDirs || DEFAULT_EXCLUDE_DIRS),
    excludeFiles: new Set(input.excludeFiles || DEFAULT_EXCLUDE_FILES),
    textExtensions: new Set(input.textExtensions || DEFAULT_OPTIONS.textExtensions),
    followSymlinks: Boolean(input.followSymlinks),
  };
}

function resolveScanPath(filePath, baseDir = process.cwd()) {
  const value = String(filePath || "").trim();
  if (!value) {
    return path.resolve(baseDir);
  }
  const expanded = value === "~" || value.startsWith(`~${path.sep}`) || value.startsWith("~/")
    ? path.join(os.homedir(), value.slice(2))
    : value;
  return path.resolve(baseDir, expanded);
}

function resolveOptionalPath(value, baseDir) {
  const text = nonEmptyString(value);
  return text ? resolveScanPath(text, baseDir) : "";
}

function resolveLocalPath(value, cwd) {
  const text = nonEmptyString(value);
  if (!text) {
    return "";
  }
  if (/^https?:\/\//i.test(text)) {
    return "";
  }
  if (!path.isAbsolute(text) && !text.startsWith(".") && !text.includes("/") && !text.includes("\\")) {
    return "";
  }
  return resolveScanPath(text, cwd);
}

function isBinaryBuffer(buffer, relativePath, options) {
  if (buffer.length === 0) {
    return false;
  }
  const ext = path.extname(relativePath).toLowerCase();
  if (options.textExtensions.has(ext)) {
    return buffer.includes(0);
  }
  if (buffer.includes(0)) {
    return true;
  }

  const sampleLength = Math.min(buffer.length, 4096);
  let suspicious = 0;
  for (let index = 0; index < sampleLength; index += 1) {
    const byte = buffer[index];
    if (byte < 7 || (byte > 14 && byte < 32)) {
      suspicious += 1;
    }
  }
  return suspicious / sampleLength > 0.3;
}

function hashFile(filePath) {
  const hash = crypto.createHash("sha256");
  const fd = fs.openSync(filePath, "r");
  const buffer = Buffer.allocUnsafe(64 * 1024);
  try {
    while (true) {
      const bytesRead = fs.readSync(fd, buffer, 0, buffer.length, null);
      if (bytesRead <= 0) {
        break;
      }
      hash.update(buffer.subarray(0, bytesRead));
    }
  } finally {
    fs.closeSync(fd);
  }
  return hash.digest("hex");
}

function safeRealpath(filePath) {
  try {
    return fs.realpathSync(filePath);
  } catch (_) {
    return null;
  }
}

function isRegularReadableFile(filePath) {
  try {
    return fs.statSync(filePath).isFile();
  } catch (_) {
    return false;
  }
}

function isDirectory(filePath) {
  try {
    return fs.statSync(filePath).isDirectory();
  } catch (_) {
    return false;
  }
}

function isPathInside(rootPath, candidatePath) {
  if (!rootPath || !candidatePath) {
    return false;
  }
  const root = safeRealpath(rootPath) || path.resolve(rootPath);
  const candidate = safeRealpath(candidatePath) || path.resolve(candidatePath);
  const relative = path.relative(root, candidate);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function toRelativePath(rootPath, filePath) {
  return normalizeRelativePath(path.relative(rootPath, filePath));
}

function normalizeRelativePath(value) {
  return String(value || "").split(path.sep).join("/");
}

function normalizePath(value) {
  return path.resolve(String(value || ""));
}

function normalizeStringArray(value, fallback = []) {
  if (!Array.isArray(value)) {
    return [...fallback];
  }
  return value
    .filter((item) => typeof item === "string" && item.trim())
    .map((item) => item.trim());
}

function nonEmptyString(value) {
  return typeof value === "string" && value.trim() ? value.trim() : "";
}

function positiveInteger(value, fallback) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return fallback;
  }
  return Math.floor(parsed);
}

function cloneJsonSafe(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch (_) {
    return String(value);
  }
}

function comparePaths(left, right) {
  return String(left).localeCompare(String(right));
}

function compareFileRecords(left, right) {
  const rank = {
    manifest: 0,
    mcp_config: 1,
    script: 2,
    schema: 3,
    text: 4,
    asset: 5,
  };
  const leftRank = rank[left.kind] ?? 9;
  const rightRank = rank[right.kind] ?? 9;
  if (leftRank !== rightRank) {
    return leftRank - rightRank;
  }
  return left.relative_path.localeCompare(right.relative_path);
}

function sortDirents(entries) {
  return [...entries].sort((left, right) => {
    const leftRank = direntRank(left.name);
    const rightRank = direntRank(right.name);
    if (leftRank !== rightRank) {
      return leftRank - rightRank;
    }
    return left.name.localeCompare(right.name);
  });
}

function direntRank(name) {
  const normalized = String(name || "").toLowerCase();
  if (["package.json", "pyproject.toml", "requirements.txt", "uv.lock", "mcp.json"].includes(normalized)) {
    return 0;
  }
  if (SCRIPT_EXTENSIONS.has(path.extname(normalized))) {
    return 1;
  }
  return 2;
}

module.exports = {
  DEFAULT_OPTIONS,
  classifyMcpFile,
  detectMcpSdkUsage,
  findMcpConfigFiles,
  normalizeTransport,
  resolveScanPath,
  scanMcpConfigFile,
  scanMcpConfigs,
  scanMcpServerDescriptor,
  scanMcpSourceDirectory,
};
