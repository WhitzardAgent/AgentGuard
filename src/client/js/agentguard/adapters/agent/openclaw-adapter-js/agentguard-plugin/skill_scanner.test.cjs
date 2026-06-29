"use strict";

const assert = require("node:assert/strict");
const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const {
  classifyFile,
  findSkillDirectories,
  parseSkillMarkdown,
  resolveScanPath,
  scanSkillDirectory,
  scanSkillRoots,
} = require("./skill_scanner.cjs");

function makeTempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "agentguard-skill-scan-"));
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

function hasSkipped(skill, relativePath, reason) {
  return skill.skipped.some((item) => item.relative_path === relativePath && item.reason === reason);
}

test("parseSkillMarkdown extracts simple frontmatter and body", () => {
  const parsed = parseSkillMarkdown([
    "---",
    "name: demo-skill",
    "description: Demo skill.",
    "version: 1",
    "tags:",
    "  - local",
    "  - safe",
    "---",
    "# Body",
    "Text",
  ].join("\n"));

  assert.deepEqual(parsed.frontmatter, {
    name: "demo-skill",
    description: "Demo skill.",
    version: 1,
    tags: ["local", "safe"],
  });
  assert.equal(parsed.body, "# Body\nText");
});

test("parseSkillMarkdown supports YAML block scalar frontmatter", () => {
  const parsed = parseSkillMarkdown([
    "---",
    "name: block-skill",
    "description: >-",
    "  First sentence.",
    "  Second sentence.",
    "",
    "  New paragraph.",
    "notes: |",
    "  line one",
    "  line two",
    "---",
    "# Body",
  ].join("\n"));

  assert.equal(parsed.frontmatter.description, "First sentence. Second sentence.\nNew paragraph.");
  assert.equal(parsed.frontmatter.notes, "line one\nline two\n");
  assert.equal(parsed.body, "# Body");
});

test("scanSkillDirectory reads normal skill files and classifies core content", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "skills", "demo");
  writeFile(path.join(skillDir, "SKILL.md"), [
    "---",
    "name: demo-skill",
    "description: Demo skill from frontmatter.",
    "category: developer",
    "---",
    "# Demo Skill",
    "Long instructions.",
  ].join("\n"));
  writeFile(path.join(skillDir, "prompt.md"), "Prompt content.");
  writeJson(path.join(skillDir, "manifest.json"), {
    name: "demo-skill",
    version: "0.1.0",
  });
  writeJson(path.join(skillDir, "schema.json"), {
    type: "object",
    properties: { query: { type: "string" } },
  });
  writeFile(path.join(skillDir, "scripts", "run.py"), "print('hello')\n");
  writeFile(path.join(skillDir, "assets", "readme.txt"), "asset note");

  const descriptor = scanSkillDirectory(skillDir);
  const files = byPath(descriptor.files);

  assert.equal(descriptor.object_type, "skill");
  assert.equal(descriptor.source_framework, "openclaw_compatible");
  assert.equal(descriptor.name, "demo-skill");
  assert.equal(descriptor.description, "Demo skill from frontmatter.");
  assert.equal(descriptor.entry_file, "SKILL.md");
  assert.equal(descriptor.extraction.level, "directory");
  assert.equal(descriptor.extraction.confidence, "high");
  assert.deepEqual(descriptor.extraction.missing, []);
  assert.equal(descriptor.manifest.version, "0.1.0");
  assert.equal(files["SKILL.md"].kind, "skill_markdown");
  assert.match(files["SKILL.md"].content, /Long instructions/);
  assert.equal(files["prompt.md"].kind, "prompt");
  assert.equal(files["manifest.json"].kind, "manifest");
  assert.equal(files["schema.json"].kind, "schema");
  assert.equal(files["scripts/run.py"].kind, "script");
  assert.equal(files["assets/readme.txt"].kind, "text");
  assert.equal(typeof descriptor.sha256, "string");
  assert.equal(descriptor.sha256.length, 64);
  assert.equal(typeof files["SKILL.md"].sha256, "string");
  assert.equal(files["SKILL.md"].sha256.length, 64);
});

test("scanSkillDirectory falls back to heading and paragraph when frontmatter is absent", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "plain-skill");
  writeFile(path.join(skillDir, "SKILL.md"), [
    "# Friendly Skill",
    "",
    "This paragraph becomes the description.",
  ].join("\n"));

  const descriptor = scanSkillDirectory(skillDir);

  assert.equal(descriptor.name, "Friendly Skill");
  assert.equal(descriptor.description, "This paragraph becomes the description.");
  assert.deepEqual(descriptor.frontmatter, {});
  assert.deepEqual(descriptor.extraction.missing, ["prompt", "manifest"]);
});

test("scanSkillDirectory records binary assets without content", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "binary-skill");
  writeFile(path.join(skillDir, "SKILL.md"), "---\nname: binary-skill\n---\n# Binary\n");
  const binary = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x00, 0x01, 0x02, 0x03]);
  writeFile(path.join(skillDir, "assets", "icon.png"), binary);

  const descriptor = scanSkillDirectory(skillDir);
  const files = byPath(descriptor.files);

  assert.equal(files["assets/icon.png"].kind, "asset");
  assert.equal(files["assets/icon.png"].binary, true);
  assert.equal(files["assets/icon.png"].content_omitted, true);
  assert.equal(files["assets/icon.png"].reason, "binary");
  assert.equal(Object.prototype.hasOwnProperty.call(files["assets/icon.png"], "content"), false);
  assert.equal(descriptor.assets.some((item) => item.relative_path === "assets/icon.png"), true);
});

test("scanSkillDirectory omits large files and respects total text byte limits", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "limit-skill");
  writeFile(path.join(skillDir, "SKILL.md"), "---\nname: limit-skill\n---\n# Limit\n");
  writeFile(path.join(skillDir, "big.md"), "x".repeat(64));
  writeFile(path.join(skillDir, "small-a.md"), "a".repeat(20));
  writeFile(path.join(skillDir, "small-b.md"), "b".repeat(20));

  const descriptor = scanSkillDirectory(skillDir, {
    maxFileBytes: 40,
    maxTotalBytesPerSkill: 70,
  });
  const files = byPath(descriptor.files);

  assert.equal(files["big.md"].content_omitted, true);
  assert.equal(files["big.md"].reason, "too_large");
  assert.equal(files["small-a.md"].content, "a".repeat(20));
  assert.equal(files["small-b.md"].content_omitted, true);
  assert.equal(files["small-b.md"].reason, "max_total_bytes_exceeded");
  assert.equal(descriptor.extraction.truncated, true);
});

test("scanSkillDirectory excludes noisy directories and files", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "exclude-skill");
  writeFile(path.join(skillDir, "SKILL.md"), "---\nname: exclude-skill\n---\n# Exclude\n");
  writeFile(path.join(skillDir, "node_modules", "pkg", "index.js"), "module.exports = {};");
  writeFile(path.join(skillDir, ".git", "config"), "[core]\n");
  writeFile(path.join(skillDir, ".DS_Store"), "noise");
  writeFile(path.join(skillDir, "keep.md"), "keep");

  const descriptor = scanSkillDirectory(skillDir);
  const files = byPath(descriptor.files);

  assert.equal(Boolean(files["keep.md"]), true);
  assert.equal(Boolean(files["node_modules/pkg/index.js"]), false);
  assert.equal(Boolean(files[".git/config"]), false);
  assert.equal(Boolean(files[".DS_Store"]), false);
  assert.equal(hasSkipped(descriptor, "node_modules", "excluded_directory"), true);
  assert.equal(hasSkipped(descriptor, ".git", "excluded_directory"), true);
  assert.equal(hasSkipped(descriptor, ".DS_Store", "excluded_file"), true);
});

test("scanSkillDirectory skips symlinks by default", { skip: process.platform === "win32" ? false : undefined }, () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "symlink-skill");
  writeFile(path.join(skillDir, "SKILL.md"), "---\nname: symlink-skill\n---\n# Symlink\n");
  writeFile(path.join(root, "outside.txt"), "outside");
  const linkPath = path.join(skillDir, "outside-link.txt");

  try {
    fs.symlinkSync(path.join(root, "outside.txt"), linkPath, "file");
  } catch (error) {
    if (process.platform === "win32") {
      return;
    }
    throw error;
  }

  const descriptor = scanSkillDirectory(skillDir);
  const files = byPath(descriptor.files);

  assert.equal(Boolean(files["outside-link.txt"]), false);
  assert.equal(hasSkipped(descriptor, "outside-link.txt", "symlink"), true);
});

test("findSkillDirectories finds top-level skills but does not recurse into discovered skill roots", () => {
  const root = makeTempRoot();
  const skillsRoot = path.join(root, "skills");
  writeFile(path.join(skillsRoot, "alpha", "SKILL.md"), "---\nname: alpha\n---\n");
  writeFile(path.join(skillsRoot, "alpha", "nested", "SKILL.md"), "---\nname: nested\n---\n");
  writeFile(path.join(skillsRoot, "group", "beta", "SKILL.md"), "---\nname: beta\n---\n");
  writeFile(path.join(skillsRoot, "node_modules", "ignored", "SKILL.md"), "---\nname: ignored\n---\n");

  const diagnostics = [];
  const found = findSkillDirectories([skillsRoot], {}, diagnostics)
    .map((item) => path.relative(skillsRoot, item).split(path.sep).join("/"))
    .sort();

  assert.deepEqual(found, ["alpha", "group/beta"]);
  assert.deepEqual(diagnostics, []);
});

test("scanSkillRoots scans multiple roots and reports unreadable diagnostics", () => {
  const root = makeTempRoot();
  const one = path.join(root, "one");
  const two = path.join(root, "two");
  writeFile(path.join(one, "SKILL.md"), "---\nname: one\n---\n");
  writeFile(path.join(two, "SKILL.md"), "---\nname: two\n---\n");

  const result = scanSkillRoots({
    roots: [one, two, path.join(root, "missing")],
  });
  const names = result.skills.map((item) => item.name).sort();

  assert.deepEqual(names, ["one", "two"]);
  assert.equal(result.summary.skill_count, 2);
  assert.equal(result.diagnostics.some((item) => item.reason === "root_unreadable"), true);
});

test("scanSkillDirectory enforces maxFilesPerSkill and keeps deterministic order", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "many-files");
  writeFile(path.join(skillDir, "SKILL.md"), "---\nname: many-files\n---\n");
  writeFile(path.join(skillDir, "a.md"), "a");
  writeFile(path.join(skillDir, "b.md"), "b");
  writeFile(path.join(skillDir, "c.md"), "c");

  const descriptor = scanSkillDirectory(skillDir, {
    maxFilesPerSkill: 3,
  });

  assert.deepEqual(descriptor.files.map((item) => item.relative_path), ["SKILL.md", "a.md", "b.md"]);
  assert.equal(hasSkipped(descriptor, "c.md", "max_files_exceeded"), true);
});

test("classifyFile identifies expected OpenClaw-compatible skill file roles", () => {
  assert.equal(classifyFile("SKILL.md"), "skill_markdown");
  assert.equal(classifyFile("prompt.md"), "prompt");
  assert.equal(classifyFile("prompts/main.md"), "prompt");
  assert.equal(classifyFile("manifest.json"), "manifest");
  assert.equal(classifyFile("skill.spec.json"), "manifest");
  assert.equal(classifyFile("input_schema.json"), "schema");
  assert.equal(classifyFile("schema.sql"), "schema");
  assert.equal(classifyFile("scripts/main.ts"), "script");
  assert.equal(classifyFile("assets/icon.png"), "asset");
});

test("resolveScanPath handles config-relative paths and home expansion", () => {
  const base = makeTempRoot();
  assert.equal(resolveScanPath("./skills", base), path.join(base, "skills"));
  assert.equal(resolveScanPath("~/skills", base), path.join(os.homedir(), "skills"));
});

test("descriptor hashes change when file contents change", () => {
  const root = makeTempRoot();
  const skillDir = path.join(root, "hash-skill");
  const skillPath = path.join(skillDir, "SKILL.md");
  writeFile(skillPath, "---\nname: hash-skill\n---\n# One\n");
  const first = scanSkillDirectory(skillDir);

  writeFile(skillPath, "---\nname: hash-skill\n---\n# Two\n");
  const second = scanSkillDirectory(skillDir);

  assert.notEqual(first.sha256, second.sha256);
  assert.notEqual(first.skill_markdown.sha256, second.skill_markdown.sha256);
  assert.equal(
    second.skill_markdown.sha256,
    crypto.createHash("sha256").update(fs.readFileSync(skillPath)).digest("hex"),
  );
});
