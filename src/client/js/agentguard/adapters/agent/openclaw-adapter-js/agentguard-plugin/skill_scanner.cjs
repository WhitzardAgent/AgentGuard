"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const ENTRY_FILE = "SKILL.md";

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
];

const DEFAULT_TEXT_EXTENSIONS = new Set([
  ".cjs",
  ".css",
  ".csv",
  ".env",
  ".html",
  ".ini",
  ".js",
  ".json",
  ".jsonl",
  ".jsx",
  ".md",
  ".mjs",
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

const DEFAULT_OPTIONS = Object.freeze({
  roots: [],
  baseDir: process.cwd(),
  maxFileBytes: 200_000,
  maxTotalBytesPerSkill: 2_000_000,
  maxFilesPerSkill: 200,
  excludeDirs: DEFAULT_EXCLUDE_DIRS,
  excludeFiles: DEFAULT_EXCLUDE_FILES,
  textExtensions: [...DEFAULT_TEXT_EXTENSIONS],
  followSymlinks: false,
});

function scanSkillRoots(options = {}) {
  const resolved = normalizeOptions(options);
  const diagnostics = [];
  const skillDirs = findSkillDirectories(resolved.roots, resolved, diagnostics);
  const skills = [];

  for (const skillDir of skillDirs) {
    try {
      skills.push(scanSkillDirectory(skillDir, resolved));
    } catch (error) {
      diagnostics.push({
        level: "error",
        path: skillDir,
        reason: "scan_failed",
        message: String(error && error.message ? error.message : error),
      });
    }
  }

  return {
    skills,
    diagnostics,
    summary: {
      roots: resolved.roots,
      skill_count: skills.length,
      diagnostic_count: diagnostics.length,
    },
  };
}

function findSkillDirectories(roots, options = {}, diagnostics = []) {
  const resolved = normalizeOptions(options);
  const out = [];
  const seen = new Set();

  for (const root of roots || []) {
    const rootPath = resolveScanPath(root, resolved.baseDir);
    let stat;
    try {
      stat = fs.lstatSync(rootPath);
    } catch (error) {
      diagnostics.push({
        level: "warning",
        path: rootPath,
        reason: "root_unreadable",
        message: String(error && error.message ? error.message : error),
      });
      continue;
    }

    if (stat.isSymbolicLink() && !resolved.followSymlinks) {
      diagnostics.push({
        level: "warning",
        path: rootPath,
        reason: "root_symlink_skipped",
      });
      continue;
    }
    if (!stat.isDirectory()) {
      diagnostics.push({
        level: "warning",
        path: rootPath,
        reason: "root_not_directory",
      });
      continue;
    }
    walkForSkillDirs(rootPath, resolved, diagnostics, out, seen);
  }

  return out.sort(comparePaths);
}

function walkForSkillDirs(dirPath, options, diagnostics, out, seen) {
  const realDir = safeRealpath(dirPath);
  if (realDir == null || seen.has(realDir)) {
    return;
  }
  seen.add(realDir);

  const entryPath = path.join(dirPath, ENTRY_FILE);
  if (isRegularReadableFile(entryPath)) {
    out.push(realDir);
    return;
  }

  let entries;
  try {
    entries = fs.readdirSync(dirPath, { withFileTypes: true });
  } catch (error) {
    diagnostics.push({
      level: "warning",
      path: dirPath,
      reason: "directory_unreadable",
      message: String(error && error.message ? error.message : error),
    });
    return;
  }

  for (const entry of sortDirents(entries)) {
    if (!entry.isDirectory()) {
      continue;
    }
    if (options.excludeDirs.has(entry.name)) {
      continue;
    }
    walkForSkillDirs(path.join(dirPath, entry.name), options, diagnostics, out, seen);
  }
}

function scanSkillDirectory(skillDir, options = {}) {
  const resolved = normalizeOptions(options);
  const rootPath = resolveScanPath(skillDir, resolved.baseDir);
  const realRoot = fs.realpathSync(rootPath);
  const entryPath = path.join(realRoot, ENTRY_FILE);
  if (!isRegularReadableFile(entryPath)) {
    throw new Error(`skill directory is missing ${ENTRY_FILE}: ${realRoot}`);
  }

  const state = {
    files: [],
    skipped: [],
    textBytesIncluded: 0,
    filesIncluded: 0,
  };

  collectSkillFiles(realRoot, realRoot, resolved, state);
  state.files.sort(compareFileRecords);
  state.skipped.sort((a, b) => String(a.relative_path || a.path).localeCompare(String(b.relative_path || b.path)));

  const skillFile = state.files.find((item) => item.relative_path === ENTRY_FILE);
  const parsedSkill = parseSkillMarkdown(skillFile && typeof skillFile.content === "string" ? skillFile.content : "");
  const firstHeading = firstMarkdownHeading(parsedSkill.body);
  const firstParagraph = firstMarkdownParagraph(parsedSkill.body);
  const name = nonEmptyString(parsedSkill.frontmatter.name)
    || nonEmptyString(parsedSkill.frontmatter.title)
    || firstHeading
    || path.basename(realRoot);
  const description = nonEmptyString(parsedSkill.frontmatter.description)
    || nonEmptyString(parsedSkill.frontmatter.summary)
    || firstParagraph
    || "";

  const manifestFile = state.files.find((item) => item.kind === "manifest" && item.parsed_json);
  const assets = state.files.filter((item) => item.kind === "asset" || item.binary === true);
  const totalSize = state.files.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const skillHash = hashSkillFileList(state.files);
  const missing = [];
  if (!state.files.some((item) => item.kind === "prompt")) {
    missing.push("prompt");
  }
  if (!manifestFile) {
    missing.push("manifest");
  }

  return {
    object_type: "skill",
    source_framework: "openclaw_compatible",
    name: String(name),
    description: String(description),
    root_path: realRoot,
    entry_file: ENTRY_FILE,
    sha256: skillHash,
    frontmatter: parsedSkill.frontmatter,
    manifest: manifestFile ? manifestFile.parsed_json : null,
    skill_markdown: skillFile || null,
    files: state.files,
    assets,
    skipped: state.skipped,
    file_count: state.files.length,
    total_size: totalSize,
    extraction: {
      level: "directory",
      confidence: "high",
      missing,
      truncated: state.files.some((item) => item.content_omitted),
    },
  };
}

function collectSkillFiles(rootPath, currentPath, options, state) {
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
          reason: "outside_skill_root",
        });
        continue;
      }
      collectSkillFiles(rootPath, fullPath, options, state);
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
    if (state.filesIncluded >= options.maxFilesPerSkill) {
      state.skipped.push({
        relative_path: relativePath,
        reason: "max_files_exceeded",
      });
      continue;
    }

    state.files.push(readSkillFile(rootPath, fullPath, options, state));
    state.filesIncluded += 1;
  }
}

function readSkillFile(rootPath, filePath, options, state) {
  const relativePath = toRelativePath(rootPath, filePath);
  const stat = fs.statSync(filePath);
  const sha256 = hashFile(filePath);
  const base = {
    relative_path: relativePath,
    kind: classifyFile(relativePath),
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
  if ((state.textBytesIncluded + stat.size) > options.maxTotalBytesPerSkill) {
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
    const parsed = parseJson(content);
    if (parsed.ok) {
      out.parsed_json = parsed.value;
    } else {
      out.parse_error = parsed.error;
    }
  }
  return out;
}

function parseSkillMarkdown(content) {
  const text = String(content || "").replace(/^\uFEFF/, "");
  const lines = text.split(/\r?\n/);
  if (lines[0] !== "---") {
    return {
      frontmatter: {},
      body: text,
    };
  }

  let end = -1;
  for (let index = 1; index < lines.length; index += 1) {
    if (lines[index] === "---") {
      end = index;
      break;
    }
  }
  if (end < 0) {
    return {
      frontmatter: {},
      body: text,
    };
  }

  const frontmatterLines = lines.slice(1, end);
  const body = lines.slice(end + 1).join("\n");
  return {
    frontmatter: parseSimpleYaml(frontmatterLines),
    body,
  };
}

function parseSimpleYaml(lines) {
  const out = {};
  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index];
    const trimmed = raw.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const match = raw.match(/^([A-Za-z0-9_.-]+):(?:\s*(.*))?$/);
    if (!match) {
      continue;
    }
    const key = match[1];
    const value = match[2] || "";
    if (value.trim()) {
      const blockStyle = parseBlockScalarStyle(value.trim());
      if (blockStyle) {
        const parsed = parseBlockScalar(lines, index + 1, blockStyle);
        out[key] = parsed.value;
        index = parsed.nextIndex - 1;
        continue;
      }
      out[key] = parseScalar(value.trim());
      continue;
    }

    const items = [];
    let cursor = index + 1;
    while (cursor < lines.length) {
      const candidate = lines[cursor];
      const candidateTrimmed = candidate.trim();
      if (!candidateTrimmed || candidateTrimmed.startsWith("#")) {
        cursor += 1;
        continue;
      }
      if (/^[A-Za-z0-9_.-]+:/.test(candidate)) {
        break;
      }
      const itemMatch = candidate.match(/^\s*-\s*(.*)$/);
      if (!itemMatch) {
        break;
      }
      items.push(parseScalar(itemMatch[1].trim()));
      cursor += 1;
    }
    out[key] = items.length ? items : "";
    index = cursor - 1;
  }
  return out;
}

function parseBlockScalarStyle(value) {
  const match = String(value || "").trim().match(/^([>|])([+-])?$/);
  if (!match) {
    return null;
  }
  return {
    type: match[1],
    chomp: match[2] || "",
  };
}

function parseBlockScalar(lines, startIndex, style) {
  const blockLines = [];
  let cursor = startIndex;
  while (cursor < lines.length) {
    const candidate = lines[cursor];
    if (isTopLevelYamlKey(candidate)) {
      break;
    }
    blockLines.push(candidate);
    cursor += 1;
  }

  const normalized = removeCommonBlockIndent(blockLines);
  const text = style.type === "|"
    ? normalized.join("\n")
    : foldBlockScalarLines(normalized);
  return {
    value: style.chomp === "-" ? text.replace(/\n+$/g, "") : text.replace(/\n*$/g, "\n"),
    nextIndex: cursor,
  };
}

function isTopLevelYamlKey(line) {
  return /^[A-Za-z0-9_.-]+:/.test(String(line || ""));
}

function removeCommonBlockIndent(lines) {
  let indent = Infinity;
  for (const line of lines) {
    if (!line.trim()) {
      continue;
    }
    const match = line.match(/^(\s*)/);
    indent = Math.min(indent, match ? match[1].length : 0);
  }
  if (!Number.isFinite(indent) || indent <= 0) {
    return [...lines];
  }
  return lines.map((line) => line.slice(0, indent).trim() ? line : line.slice(indent));
}

function foldBlockScalarLines(lines) {
  const parts = [];
  let current = [];
  let blankRun = 0;
  for (const line of lines) {
    if (!line.trim()) {
      if (current.length) {
        parts.push(current.join(" "));
        current = [];
      }
      blankRun += 1;
      continue;
    }
    if (blankRun > 0 && parts.length) {
      parts.push("\n".repeat(blankRun));
    }
    blankRun = 0;
    current.push(line.trim());
  }
  if (current.length) {
    parts.push(current.join(" "));
  }
  return parts.join("");
}

function parseScalar(value) {
  const text = String(value || "").trim();
  if (!text) {
    return "";
  }
  if ((text.startsWith("\"") && text.endsWith("\"")) || (text.startsWith("'") && text.endsWith("'"))) {
    return text.slice(1, -1);
  }
  if (text === "true") {
    return true;
  }
  if (text === "false") {
    return false;
  }
  if (text === "null") {
    return null;
  }
  if (/^-?\d+(?:\.\d+)?$/.test(text)) {
    return Number(text);
  }
  if (text.startsWith("[") && text.endsWith("]")) {
    try {
      const parsed = JSON.parse(text.replace(/'/g, "\""));
      if (Array.isArray(parsed)) {
        return parsed;
      }
    } catch (_) {
      return text;
    }
  }
  return text;
}

function firstMarkdownHeading(content) {
  for (const line of String(content || "").split(/\r?\n/)) {
    const match = line.match(/^#\s+(.+)$/);
    if (match && match[1].trim()) {
      return match[1].trim();
    }
  }
  return "";
}

function firstMarkdownParagraph(content) {
  for (const line of String(content || "").split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith("```")) {
      continue;
    }
    return trimmed;
  }
  return "";
}

function classifyFile(relativePath) {
  const normalized = normalizeRelativePath(relativePath).toLowerCase();
  const name = path.posix.basename(normalized);
  const ext = path.posix.extname(normalized);
  if (normalized === "skill.md") {
    return "skill_markdown";
  }
  if (name === "prompt.md" || normalized.startsWith("prompts/")) {
    return "prompt";
  }
  if (
    name === "manifest.json"
    || name === "manifest.yaml"
    || name === "manifest.yml"
    || name === "skill.json"
    || name === "skill.spec.json"
    || name === "skill.spec.yaml"
    || name === "skill.spec.yml"
  ) {
    return "manifest";
  }
  if (name.includes("schema") && [".json", ".yaml", ".yml", ".sql"].includes(ext)) {
    return "schema";
  }
  if ([".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".sh"].includes(ext)) {
    return "script";
  }
  if ([".md", ".txt"].includes(ext)) {
    return "text";
  }
  return "asset";
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

function hashSkillFileList(files) {
  const hash = crypto.createHash("sha256");
  for (const file of [...files].sort((a, b) => a.relative_path.localeCompare(b.relative_path))) {
    hash.update(file.relative_path);
    hash.update("\0");
    hash.update(file.sha256 || "");
    hash.update("\0");
  }
  return hash.digest("hex");
}

function parseJson(content) {
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
    maxFileBytes: positiveInteger(input.maxFileBytes, DEFAULT_OPTIONS.maxFileBytes),
    maxTotalBytesPerSkill: positiveInteger(input.maxTotalBytesPerSkill, DEFAULT_OPTIONS.maxTotalBytesPerSkill),
    maxFilesPerSkill: positiveInteger(input.maxFilesPerSkill, DEFAULT_OPTIONS.maxFilesPerSkill),
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

function isPathInside(rootPath, candidatePath) {
  if (!candidatePath) {
    return false;
  }
  const relative = path.relative(rootPath, candidatePath);
  return relative === "" || (!relative.startsWith("..") && !path.isAbsolute(relative));
}

function toRelativePath(rootPath, filePath) {
  return normalizeRelativePath(path.relative(rootPath, filePath));
}

function normalizeRelativePath(value) {
  return String(value || "").split(path.sep).join("/");
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

function comparePaths(left, right) {
  return String(left).localeCompare(String(right));
}

function compareFileRecords(left, right) {
  if (left.relative_path === ENTRY_FILE) {
    return -1;
  }
  if (right.relative_path === ENTRY_FILE) {
    return 1;
  }
  return left.relative_path.localeCompare(right.relative_path);
}

function sortDirents(entries) {
  return [...entries].sort((left, right) => {
    if (left.name === ENTRY_FILE) {
      return -1;
    }
    if (right.name === ENTRY_FILE) {
      return 1;
    }
    return left.name.localeCompare(right.name);
  });
}

module.exports = {
  DEFAULT_OPTIONS,
  ENTRY_FILE,
  classifyFile,
  findSkillDirectories,
  parseSkillMarkdown,
  resolveScanPath,
  scanSkillDirectory,
  scanSkillRoots,
};
