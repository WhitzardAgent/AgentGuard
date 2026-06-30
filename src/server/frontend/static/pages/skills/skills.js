(function () {
  const data = window.AgentGuardData;
  const shell = window.AgentGuardShell;
  const api = window.AgentGuardApi;
  const i18n = window.AgentGuardI18n;

  const selectedAgentLabel = document.getElementById("skill-selected-agent");
  const syncStatus = document.getElementById("skill-sync-status");
  const refreshButton = document.getElementById("refresh-skills");
  const selectAllButton = document.getElementById("select-all-skills");
  const clearSelectionButton = document.getElementById("clear-skill-selection");
  const detectButton = document.getElementById("detect-selected-skills");
  const useLlmInput = document.getElementById("skill-use-llm");
  const llmConcurrencyInput = document.getElementById("skill-llm-concurrency");
  const skillList = document.getElementById("skill-list");
  const selectionCount = document.getElementById("skill-selection-count");
  const totalCount = document.getElementById("skill-count-total");
  const riskyCount = document.getElementById("skill-count-risky");
  const fileCount = document.getElementById("skill-count-files");

  const state = {
    selectedAgentId: String(shell?.getState?.().selectedAgentId || "").trim(),
    skills: [],
    selected: new Set(),
    expanded: new Set(),
    loading: false,
    detecting: false,
    detectStartedAt: 0,
    detectElapsedS: 0,
    detectTimer: null,
    pendingSkillIds: new Set(),
    pendingRuleIds: new Set(),
    pendingLlmIds: new Set(),
    waitingLlmIds: new Set(),
    pendingUseLlm: false,
    selectedFiles: new Map(),
    detectionError: "",
  };

  shell?.setPageContext({
    title: "Skill Security",
    description: "Inspect reported skills and run static detection for the selected agent.",
  });

  function t(value) {
    return i18n?.t?.(value) || value;
  }

  function isZh() {
    return i18n?.getLanguage?.() === "zh";
  }

  function showToast(message, tone) {
    window.AgentGuardUI?.showToast?.(message, tone);
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function formatBytes(value) {
    const bytes = Number(value || 0);
    if (!Number.isFinite(bytes) || bytes <= 0) {
      return "0 B";
    }
    if (bytes < 1024) {
      return `${bytes} B`;
    }
    if (bytes < 1024 * 1024) {
      return `${(bytes / 1024).toFixed(1)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function formatElapsed(seconds) {
    const normalized = Math.max(0, Math.floor(Number(seconds || 0)));
    const minutes = Math.floor(normalized / 60);
    const rest = normalized % 60;
    return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
  }

  function clampConcurrency(value) {
    const number = Number(value);
    return [1, 2, 4, 8].includes(number) ? number : 1;
  }

  function plural(count, singular, pluralValue) {
    return count === 1 ? singular : pluralValue;
  }

  function skillId(skill) {
    return String(skill?.skill_unique_id || "").trim();
  }

  function withoutDetectionResults(skills) {
    return (Array.isArray(skills) ? skills : []).map((skill) => ({
      ...skill,
      detect_result: null,
    }));
  }

  function isPendingSkill(skill) {
    const id = skillId(skill);
    return Boolean(id && state.pendingSkillIds.has(id));
  }

  function isRulePendingSkill(skill) {
    const id = skillId(skill);
    return Boolean(id && state.pendingRuleIds.has(id));
  }

  function isLlmPendingSkill(skill) {
    const id = skillId(skill);
    return Boolean(id && state.pendingLlmIds.has(id));
  }

  function isLlmWaitingSkill(skill) {
    const id = skillId(skill);
    return Boolean(id && state.waitingLlmIds.has(id));
  }

  function hasDetection(skill) {
    return Boolean(skill?.detect_result);
  }

  function detectLabel(skill) {
    if (isPendingSkill(skill)) {
      return "running";
    }
    if (!hasDetection(skill)) {
      return "not_detected";
    }
    return String(skill?.detect_result?.label || "").trim().toLowerCase() || "not_detected";
  }

  function labelClass(label) {
    if (label === "malicious") {
      return "malicious";
    }
    if (label === "suspicious") {
      return "suspicious";
    }
    if (label === "benign") {
      return "benign";
    }
    if (label === "running") {
      return "running";
    }
    return "none";
  }

  function labelPillClass(label) {
    if (label === "malicious") {
      return "pill danger skill-result-pill";
    }
    if (label === "suspicious") {
      return "pill warn skill-result-pill";
    }
    if (label === "benign") {
      return "pill skill-result-pill skill-result-benign";
    }
    return "pill skill-result-pill skill-result-none";
  }

  function visibleLabel(label) {
    const labels = {
      not_detected: t("not detected"),
      malicious: t("malicious"),
      suspicious: t("suspicious"),
      benign: t("benign"),
    };
    return labels[label] || label || t("not detected");
  }

  function labelTextPair(label) {
    const normalized = String(label || "not_detected").trim().toLowerCase();
    const pairs = {
      malicious: { en: "malicious", zh: "恶意" },
      suspicious: { en: "suspicious", zh: "可疑" },
      benign: { en: "benign", zh: "良性" },
      running: { en: "detecting", zh: "检测中" },
      not_detected: { en: "not detected", zh: "未检测" },
      not_requested: { en: "not requested", zh: "未请求" },
      not_configured: { en: "not configured", zh: "未配置" },
      failed: { en: "failed", zh: "失败" },
      completed: { en: "completed", zh: "已完成" },
      skipped: { en: "skipped", zh: "已跳过" },
    };
    return pairs[normalized] || { en: normalized || "unknown", zh: t(normalized || "unknown") };
  }

  function renderDualLabel(label, className = "") {
    const pair = labelTextPair(label);
    const text = isZh() ? pair.zh : pair.en;
    return `
      <span class="skill-dual-label ${className}">
        <span>${escapeHtml(text)}</span>
      </span>
    `;
  }

  function sortedSkills() {
    return state.skills.slice().sort((a, b) => {
      const labelOrder = { running: 0, malicious: 1, suspicious: 2, not_detected: 3, benign: 4 };
      const left = labelOrder[detectLabel(a)] ?? 4;
      const right = labelOrder[detectLabel(b)] ?? 4;
      if (left !== right) {
        return left - right;
      }
      return String(a.name || "").localeCompare(String(b.name || ""));
    });
  }

  function filesForSkill(skill) {
    return Array.isArray(skill?.skill_resource?.files) ? skill.skill_resource.files : [];
  }

  function selectedFilePath(skill) {
    const id = skillId(skill);
    const files = filesForSkill(skill);
    if (!files.length) {
      return "";
    }
    const stored = state.selectedFiles.get(id);
    if (stored && files.some((file) => file.relative_path === stored)) {
      return stored;
    }
    const preferred = files.find((file) => file.relative_path === "SKILL.md")
      || files.find((file) => typeof file.content === "string")
      || files[0];
    return preferred?.relative_path || "";
  }

  function selectedFile(skill) {
    const path = selectedFilePath(skill);
    return filesForSkill(skill).find((file) => file.relative_path === path) || null;
  }

  function skillMarkdown(skill) {
    const direct = skill?.skill_resource?.skill_markdown;
    if (direct && typeof direct.content === "string") {
      return direct.content;
    }
    const found = filesForSkill(skill).find((file) => file.relative_path === "SKILL.md");
    return typeof found?.content === "string" ? found.content : "";
  }

  function excerpt(value, maxChars = 560) {
    const text = String(value || "").trim();
    if (!text) {
      return "";
    }
    return text.length > maxChars ? `${text.slice(0, maxChars)}...` : text;
  }

  function ruleBased(skill) {
    const rule = skill?.detect_result?.metadata?.rule_based;
    return rule && typeof rule === "object" ? rule : null;
  }

  function ruleSignals(skill) {
    const signals = ruleBased(skill)?.parsed_summary?.signals;
    return Array.isArray(signals) ? signals : [];
  }

  function ruleFindingCount(skill) {
    const count = Number(ruleBased(skill)?.finding_count);
    if (Number.isFinite(count) && count >= 0) {
      return count;
    }
    return ruleSignals(skill).length;
  }

  function ruleCategory(skill) {
    return String(ruleBased(skill)?.category || ruleBased(skill)?.parsed_summary?.category || "").trim();
  }

  function ruleConfidence(skill) {
    const value = Number(ruleBased(skill)?.confidence);
    if (!Number.isFinite(value) || value <= 0) {
      return "";
    }
    return `${Math.round(value * 100)}%`;
  }

  function llmReview(skill) {
    const review = skill?.detect_result?.metadata?.llm_review;
    return review && typeof review === "object" ? review : null;
  }

  function ruleLabel(skill) {
    if (isRulePendingSkill(skill)) {
      return "running";
    }
    if (!hasDetection(skill)) {
      return "not_detected";
    }
    return String(ruleBased(skill)?.label || detectLabel(skill) || "not_detected").trim().toLowerCase();
  }

  function llmLabel(skill) {
    if (isLlmPendingSkill(skill)) {
      return "running";
    }
    if (isLlmWaitingSkill(skill)) {
      return "running";
    }
    if (!hasDetection(skill)) {
      return "not_detected";
    }
    const review = llmReview(skill);
    if (!review) {
      return "not_requested";
    }
    if (review.skipped) {
      return "not_configured";
    }
    if (review.error) {
      return "failed";
    }
    return String(review.label || "completed").trim().toLowerCase();
  }

  function fileLocation(signal) {
    const path = String(signal?.file_path || "").trim();
    const line = Number(signal?.line_number || 0);
    if (!path) {
      return "";
    }
    return line > 0 ? `${path}:${line}` : path;
  }

  function signalDisplayName(signal) {
    const id = String(signal?.signal_id || "").trim();
    const known = {
      SA001_SENSITIVE_FILE_READ: ["Sensitive file read", "敏感文件读取"],
      SA002_SENSITIVE_PATH_REFERENCE: ["Sensitive path reference", "敏感路径引用"],
      SA003_SECRET_LITERAL: ["Secret literal", "密钥文本"],
      SA004_IDENTITY_FILE: ["Identity file access", "身份文件访问"],
      SA005_PERSONAL_DATA_SCOPE: ["Personal data scope", "个人数据范围"],
      NET001_NETWORK_CALL: ["Network call", "网络请求"],
      NET002_NETWORK_DESTINATION: ["Network destination", "网络目标"],
      NET003_NETWORK_WITH_SENSITIVE_DATA: ["Sensitive data over network", "敏感数据联网"],
      NET004_EXFILTRATION_LANGUAGE: ["Exfiltration language", "外传意图语言"],
      NET005_C2_CHANNEL: ["C2 channel", "控制通道"],
      NET006_SENSITIVE_QUERY_LEAK: ["Sensitive query leak", "敏感查询泄露"],
      NET007_FILE_UPLOAD_EGRESS: ["File upload egress", "文件上传外发"],
      EX001_COMMAND_INVOCATION: ["Command invocation", "命令调用"],
      EX002_CODE_EXECUTION_PRIMITIVE: ["Code execution primitive", "代码执行能力"],
      EX003_UNSAFE_DESERIALIZATION: ["Unsafe deserialization", "不安全反序列化"],
      EX004_REMOTE_CODE_EXECUTION_PIPE: ["Remote code execution pipe", "远程代码执行管道"],
      EX005_REMOTE_CODE_EXECUTION_COMBO: ["Remote code execution combo", "远程代码执行组合"],
      EX006_UNSAFE_COMMAND_CONSTRUCTION: ["Unsafe command construction", "不安全命令拼接"],
      DEP001_FLOATING_DEPENDENCY: ["Floating dependency", "浮动依赖"],
      DEP002_INSTALL_HOOK: ["Install hook", "安装钩子"],
      DEP003_HIDDEN_CODE_FILE: ["Hidden code file", "隐藏代码文件"],
      DEP004_REMOTE_DEPENDENCY: ["Remote dependency", "远程依赖"],
      OBF001_ENCODED_BLOB: ["Encoded blob", "编码载荷"],
      OBF002_ZERO_WIDTH: ["Zero-width characters", "零宽字符"],
      OBF003_DECODE_EXECUTE_COMBO: ["Decode and execute combo", "解码执行组合"],
      PRIV001_OVERPRIVILEGED_CAPABILITY: ["Overprivileged capability", "过高权限"],
      PER002_HOST_PERSISTENCE: ["Host persistence", "主机持久化"],
    };
    if (known[id]) {
      return { en: known[id][0], zh: known[id][1] };
    }
    const readable = id
      ? id.replace(/^[A-Z]+[0-9]+_/, "").replace(/_/g, " ").toLowerCase()
      : String(signal?.kind || "rule signal").replace(/_/g, " ");
    return {
      en: readable.replace(/\b\w/g, (char) => char.toUpperCase()),
      zh: readable,
    };
  }

  function signalMeaning(signal) {
    const id = String(signal?.signal_id || "").trim();
    const meanings = {
      SA001_SENSITIVE_FILE_READ: [
        "The skill contains behavior that reads sensitive local files, such as credentials, SSH keys, environment files, or other secret-bearing paths. This is a strong risk signal, but it is not proof of data exfiltration by itself.",
        "该 Skill 存在读取敏感本地文件的行为，例如凭据、SSH key、环境变量文件或其他可能包含密钥的路径。这是较强风险信号，但单独看不等于已经外传数据。",
      ],
      SA002_SENSITIVE_PATH_REFERENCE: [
        "The skill mentions sensitive paths. This is weaker than an actual read operation, but it can become important when combined with network, upload, or execution behavior.",
        "该 Skill 提到了敏感路径。它弱于实际读取行为，但如果同时出现联网、上传或执行行为，就会变得重要。",
      ],
      SA003_SECRET_LITERAL: [
        "The scanner found text shaped like a secret or credential. The value is redacted or summarized before display.",
        "扫描器发现疑似密钥或凭据格式的文本。展示前会做脱敏或摘要。",
      ],
      NET001_NETWORK_CALL: [
        "The skill appears to make network requests. This is a risk factor when combined with sensitive data collection or command execution.",
        "该 Skill 可能会发起网络请求。当它与敏感数据收集或命令执行组合出现时，风险更高。",
      ],
      NET003_NETWORK_WITH_SENSITIVE_DATA: [
        "The scanner observed network behavior in the same context as sensitive data access.",
        "扫描器发现联网行为与敏感数据访问出现在同一上下文中。",
      ],
      NET004_EXFILTRATION_LANGUAGE: [
        "The skill text contains language that suggests sending, uploading, forwarding, or exfiltrating data.",
        "Skill 文本中出现发送、上传、转发或外传数据相关表述。",
      ],
      EX001_COMMAND_INVOCATION: [
        "The skill can invoke shell or system commands.",
        "该 Skill 具备调用 shell 或系统命令的能力。",
      ],
      EX002_CODE_EXECUTION_PRIMITIVE: [
        "The skill contains primitives that can execute code dynamically.",
        "该 Skill 包含动态执行代码的能力。",
      ],
    };
    if (meanings[id]) {
      return isZh() ? meanings[id][1] : meanings[id][0];
    }
    const displayName = signalDisplayName(signal);
    const kind = String(signal?.kind || "").replace(/_/g, " ").trim();
    if (isZh()) {
      return `${displayName.en} 类型的规则信号。请结合命中位置和证据判断它是否构成真实风险。`;
    }
    return `${displayName.en} rule signal. Review the locations and evidence to decide whether it represents a real risk.`;
  }

  function signalGroups(skill) {
    const groups = new Map();
    ruleSignals(skill).forEach((signal) => {
      const key = String(signal?.signal_id || signal?.kind || "rule signal").trim();
      if (!groups.has(key)) {
        groups.set(key, {
          key,
          signal,
          count: 0,
          maxSeverity: 0,
          maxConfidence: 0,
          files: new Set(),
        });
      }
      const group = groups.get(key);
      group.count += 1;
      group.maxSeverity = Math.max(group.maxSeverity, Number(signal?.severity || 0));
      group.maxConfidence = Math.max(group.maxConfidence, Number(signal?.confidence || 0));
      if (signal?.file_path) {
        group.files.add(String(signal.file_path));
      }
    });
    return [...groups.values()].sort((left, right) => (
      right.count - left.count
      || right.maxSeverity - left.maxSeverity
      || right.maxConfidence - left.maxConfidence
      || left.key.localeCompare(right.key)
    ));
  }

  function affectedFileCount(skill) {
    const files = new Set();
    ruleSignals(skill).forEach((signal) => {
      if (signal?.file_path) {
        files.add(String(signal.file_path));
      }
    });
    return files.size;
  }

  function topSignalText(skill, limit = 2) {
    const groups = signalGroups(skill).slice(0, limit);
    if (!groups.length) {
      return "";
    }
    return groups.map((group) => {
      const name = signalDisplayName(group.signal);
      return `${name.en} x${group.count}`;
    }).join(isZh() ? "、" : ", ");
  }

  function mainReason(skill) {
    if (!hasDetection(skill)) {
      return t("No detection has run. Select this skill and click Detect selected.");
    }

    const review = llmReview(skill);
    if (review && !review.skipped && review.reason) {
      return String(review.reason);
    }

    const signals = ruleSignals(skill);
    if (signals.length) {
      const first = signals[0] || {};
      const signalId = String(first.signal_id || first.kind || "rule").trim();
      const location = fileLocation(first);
      if (isZh()) {
        return location
          ? `规则扫描命中 ${signalId}，位置 ${location}。`
          : `规则扫描命中 ${signalId}。`;
      }
      return location
        ? `Rule-based scan matched ${signalId} at ${location}.`
        : `Rule-based scan matched ${signalId}.`;
    }

    if (detectLabel(skill) === "benign") {
      return t("No high-confidence risk signals were found.");
    }
    return excerpt(skill?.detect_result?.reason || ruleBased(skill)?.reason || t("Static detection completed."), 220);
  }

  function ruleSummaryText(skill) {
    if (isRulePendingSkill(skill)) {
      return `${t("Rule-based scan is running.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    if (!hasDetection(skill)) {
      return t("Run detection to see rule-based conclusions.");
    }

    const signals = ruleSignals(skill);
    if (signals.length) {
      const label = labelTextPair(ruleLabel(skill));
      const findingCount = ruleFindingCount(skill);
      const fileCountValue = affectedFileCount(skill);
      const topSignals = topSignalText(skill, 3);
      if (isZh()) {
        return `规则扫描判定为${label.zh}，发现 ${findingCount} 个信号，涉及 ${fileCountValue || 1} 个文件。${topSignals ? `主要类型：${topSignals}。` : ""}`;
      }
      return `Rule-based scanner classified this as ${label.en}. It found ${findingCount} signals across ${fileCountValue || 1} file(s).${topSignals ? ` Main signal types: ${topSignals}.` : ""}`;
    }

    const reason = String(ruleBased(skill)?.reason || skill?.detect_result?.reason || "").trim();
    if (reason) {
      return excerpt(reason, 220);
    }
    return t("No high-confidence risk signals were found.");
  }

  function llmStatusText(skill) {
    if (isLlmPendingSkill(skill)) {
      return `${t("Waiting for LLM response. Do not click Detect again.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    if (isLlmWaitingSkill(skill)) {
      if (isRulePendingSkill(skill)) {
        return `${t("Rule-based scan is running. LLM review will start after it finishes.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
      }
      return `${t("Rule-based result is ready. Waiting for an LLM review slot.")} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
    }
    const review = llmReview(skill);
    if (!hasDetection(skill)) {
      return t("Run detection with LLM review enabled to see the LLM conclusion.");
    }
    if (!review) {
      return t("Rule-based result only. LLM review was not requested.");
    }
    if (review.skipped) {
      return t("LLM review is not configured. Rule-based result is shown.");
    }
    if (review.error) {
      return `${t("LLM review failed")}: ${review.error}`;
    }
    if (review.reason) {
      return review.reason;
    }
    return t("LLM review completed without a separate reason.");
  }

  function renderMetrics() {
    const skills = state.skills;
    const risky = skills.filter((skill) => ["suspicious", "malicious"].includes(detectLabel(skill))).length;
    const files = skills.reduce((sum, skill) => sum + Number(skill.file_count || 0), 0);
    totalCount.textContent = String(skills.length);
    riskyCount.textContent = String(risky);
    fileCount.textContent = String(files);
  }

  function renderSelection() {
    const selected = state.selected.size;
    selectionCount.textContent = t(`${selected} selected`);
    detectButton.disabled = state.detecting || state.loading || selected === 0;
    detectButton.textContent = state.detecting
      ? `${t("Detecting")} ${formatElapsed(state.detectElapsedS)}`
      : t("Detect selected");
    detectButton.classList.toggle("is-loading", state.detecting);
    clearSelectionButton.disabled = state.detecting || selected === 0;
    selectAllButton.disabled = state.detecting || state.loading || state.skills.length === 0;
    if (useLlmInput) {
      useLlmInput.disabled = state.detecting;
    }
    if (llmConcurrencyInput) {
      llmConcurrencyInput.disabled = state.detecting;
    }
  }

  function renderStatus(message = "") {
    selectedAgentLabel.textContent = state.selectedAgentId || t("the selected agent");
    if (message) {
      syncStatus.textContent = t(message);
      return;
    }
    if (!state.selectedAgentId) {
      syncStatus.textContent = t("Choose an agent first.");
      return;
    }
    if (state.loading) {
      syncStatus.textContent = t(`Loading skills for ${state.selectedAgentId}...`);
      return;
    }
    if (state.detecting) {
      const waitText = state.pendingUseLlm ? "Waiting for LLM response..." : "Running static detection...";
      syncStatus.textContent = `${t(waitText)} ${t("Elapsed")}: ${formatElapsed(state.detectElapsedS)}.`;
      return;
    }
    const syncedAt = data?.getLastSkillSyncTime?.();
    if (syncedAt) {
      syncStatus.textContent = t(`Loaded ${state.skills.length} skills for ${state.selectedAgentId}. Last updated: ${syncedAt}`);
    } else {
      syncStatus.textContent = t("Loaded skill catalog just now.");
    }
  }

  function renderSignalSummary(skill) {
    if (isRulePendingSkill(skill)) {
      const chips = [
        `<span class="skill-pending-chip"><span class="skill-mini-spinner" aria-hidden="true"></span>${escapeHtml(t("Detection running"))}</span>`,
        `<span>${escapeHtml(t("Elapsed"))}: ${escapeHtml(formatElapsed(state.detectElapsedS))}</span>`,
      ];
      return chips.join("");
    }
    const findingCount = ruleFindingCount(skill);
    if (!hasDetection(skill)) {
      return `<span>${escapeHtml(t("Not run"))}</span>`;
    }
    const chips = [`<span>${escapeHtml(t(`${findingCount} rule signals`))}</span>`];
    const fileCountValue = affectedFileCount(skill);
    if (fileCountValue) {
      chips.push(`<span>${escapeHtml(t(`${fileCountValue} affected files`))}</span>`);
    }
    const category = ruleCategory(skill);
    if (category) {
      chips.push(`<span>${escapeHtml(t("Category"))}: ${escapeHtml(category)}</span>`);
    }
    const confidence = ruleConfidence(skill);
    if (confidence) {
      chips.push(`<span>${escapeHtml(t("Confidence"))}: ${escapeHtml(confidence)}</span>`);
    }
    const topGroup = signalGroups(skill)[0];
    if (topGroup) {
      const name = signalDisplayName(topGroup.signal);
      chips.push(`<span>${escapeHtml(t("Top signal"))}: ${escapeHtml(name.en)} x${escapeHtml(topGroup.count)}</span>`);
    }
    return chips.join("");
  }

  function renderConclusionCard({ title, label, body, meta, tone = "" }) {
    return `
      <section class="skill-conclusion-card ${tone ? `skill-conclusion-${tone}` : ""}">
        <div class="skill-conclusion-head">
          <span>${escapeHtml(title)}</span>
          ${renderDualLabel(label, `skill-dual-label-${labelClass(label)}`)}
        </div>
        <p>${escapeHtml(body)}</p>
        ${meta ? `<div class="skill-conclusion-meta">${meta}</div>` : ""}
      </section>
    `;
  }

  function renderResultBanner(skill) {
    const label = detectLabel(skill);
    const ruleMeta = hasDetection(skill)
      ? `
        ${renderSignalSummary(skill)}
      `
      : `<span>${escapeHtml(t("Not run"))}</span>`;
    const llmReviewValue = llmReview(skill);
    const llmMeta = llmReviewValue?.skipped
      ? `<span>${escapeHtml(t("Configure environment variables to enable LLM review."))}</span>`
      : "";
    return `
      <section class="skill-result-banner skill-result-${labelClass(label)}" aria-label="${escapeHtml(t("Detection Result"))}">
        <div class="skill-final-label-card">
          <span class="skill-result-kicker">${escapeHtml(t("Detection Result"))}</span>
          <div class="skill-final-label">
            ${renderDualLabel(label, `skill-dual-label-${labelClass(label)}`)}
          </div>
        </div>
        <div class="skill-conclusion-grid">
          ${renderConclusionCard({
            title: t("Rule-based conclusion"),
            label: ruleLabel(skill),
            body: ruleSummaryText(skill),
            meta: ruleMeta,
            tone: labelClass(ruleLabel(skill)),
          })}
          ${renderConclusionCard({
            title: t("LLM conclusion"),
            label: llmLabel(skill),
            body: llmStatusText(skill),
            meta: llmMeta,
            tone: labelClass(llmLabel(skill)),
          })}
        </div>
      </section>
    `;
  }

  function renderRuleFindings(skill) {
    const signals = ruleSignals(skill);
    if (isRulePendingSkill(skill)) {
      return `<div class="empty-state skill-detail-empty">${escapeHtml(t("Detection is running. Results will appear here when the server responds."))}</div>`;
    }
    if (!hasDetection(skill)) {
      return `<div class="empty-state skill-detail-empty">${escapeHtml(t("Run detection to see rule findings."))}</div>`;
    }
    if (!signals.length) {
      return `<div class="empty-state skill-detail-empty">${escapeHtml(t("No rule-based signal details were returned."))}</div>`;
    }
    return signalGroups(skill).slice(0, 8).map((group) => {
      const displayName = signalDisplayName(group.signal);
      const examples = signals
        .filter((signal) => String(signal?.signal_id || signal?.kind || "rule signal").trim() === group.key)
        .slice(0, 3);
      return `
        <div class="skill-signal-row">
          <div class="skill-signal-head">
            <div>
              <strong>${escapeHtml(displayName.en)}</strong>
              <code>${escapeHtml(group.key)}</code>
            </div>
            <span>${escapeHtml(t(`${group.count} matches`))}</span>
          </div>
          <p>${escapeHtml(signalMeaning(group.signal))}</p>
          <div class="skill-signal-examples">
            ${examples.map((signal) => {
              const location = fileLocation(signal);
              return `
                <div>
                  ${location ? `<span>${escapeHtml(location)}</span>` : ""}
                  <p>${escapeHtml(excerpt(signal.evidence || "", 260))}</p>
                </div>
              `;
            }).join("")}
          </div>
        </div>
      `;
    }).join("");
  }

  function renderRawDetectionDetails(skill) {
    const reason = String(skill?.detect_result?.reason || ruleBased(skill)?.reason || "").trim();
    if (!reason) {
      return "";
    }
    return `
      <details class="skill-raw-details">
        <summary>${escapeHtml(t("Raw detector output"))}</summary>
        <pre>${escapeHtml(reason)}</pre>
      </details>
    `;
  }

  function renderFileList(skill) {
    const files = filesForSkill(skill);
    if (!files.length) {
      return `<div class="empty-state skill-detail-empty">${escapeHtml(t("No files were reported for this skill."))}</div>`;
    }
    const activePath = selectedFilePath(skill);
    const id = skillId(skill);
    return `
      <div class="skill-file-table" role="table" aria-label="${escapeHtml(t("File list"))}">
        <div class="skill-file-row skill-file-head" role="row">
          <span>${escapeHtml(t("Path"))}</span>
          <span>${escapeHtml(t("Type"))}</span>
          <span>${escapeHtml(t("Size"))}</span>
        </div>
        ${files.map((file) => {
      const omitted = file.content_omitted ? ` · ${file.content_omitted}` : "";
      return `
        <button class="skill-file-row skill-file-button ${file.relative_path === activePath ? "active" : ""}" type="button" role="row" data-action="select-file" data-skill-id="${escapeHtml(id)}" data-file-path="${escapeHtml(file.relative_path || "")}">
          <code>${escapeHtml(file.relative_path || "-")}</code>
          <span>${escapeHtml(t(file.kind || "file"))}</span>
          <span>${formatBytes(file.size || 0)}${escapeHtml(omitted)}</span>
        </button>
      `;
    }).join("")}
      </div>
    `;
  }

  function renderSelectedFilePreview(skill) {
    const file = selectedFile(skill);
    if (!file) {
      return `<pre class="skill-code-preview skill-code-empty">${escapeHtml(t("Select a file to preview its content."))}</pre>`;
    }
    const path = file.relative_path || t("Unnamed file");
    const content = typeof file.content === "string" ? file.content : "";
    const unavailableReason = file.binary
      ? t("Binary files cannot be previewed in the browser.")
      : (file.content_omitted
        ? `${t("File content was omitted by the adapter")}: ${file.content_omitted}`
        : t("No previewable content was reported for this file."));
    return `
      <div class="skill-preview-head">
        <code>${escapeHtml(path)}</code>
        <span>${escapeHtml(t(file.kind || "file"))} · ${formatBytes(file.size || 0)}</span>
      </div>
      <pre class="skill-code-preview ${content ? "" : "skill-code-empty"}">${escapeHtml(content || unavailableReason)}</pre>
    `;
  }

  function renderContentPreview(skill) {
    const rootPath = skill.root_path ? `<code>${escapeHtml(skill.root_path)}</code>` : `<span>${escapeHtml(t("No root path reported."))}</span>`;
    const id = skillId(skill);
    return `
      <div class="skill-content-section">
        <div class="skill-content-header">
          <div>
            <h4>${escapeHtml(t("Skill content"))}</h4>
            <p class="subtle">${escapeHtml(t("Original files collected by the adapter for this skill."))}</p>
          </div>
          <div class="skill-content-meta">
            <span class="pill">${escapeHtml(t(`${Number(skill.file_count || filesForSkill(skill).length)} files`))}</span>
            <span class="pill">${formatBytes(skill.total_size)}</span>
            <button class="btn skill-download-button" type="button" data-action="download-skill" data-skill-id="${escapeHtml(id)}">${escapeHtml(t("Download skill"))}</button>
          </div>
        </div>
        <div class="skill-content-layout">
          <section class="skill-content-panel skill-markdown-panel">
            <h5>${escapeHtml(t("File preview"))}</h5>
            ${renderSelectedFilePreview(skill)}
          </section>
          <section class="skill-content-panel skill-files-panel">
            <div class="skill-content-root">
              <strong>${escapeHtml(t("Root path"))}</strong>
              ${rootPath}
            </div>
            <h5>${escapeHtml(t("File list"))}</h5>
            <div class="skill-file-list">${renderFileList(skill)}</div>
          </section>
        </div>
      </div>
    `;
  }

  function renderSkillDetails(skill) {
    return `
      <div class="skill-details">
        <section class="skill-detail-panel skill-findings-panel">
          <div class="skill-section-heading">
            <h4>${escapeHtml(t("Rule-based findings"))}</h4>
            <span>${escapeHtml(t(`${ruleFindingCount(skill)} rule signals`))}</span>
          </div>
          <div class="skill-finding-list">${renderRuleFindings(skill)}</div>
          ${renderRawDetectionDetails(skill)}
        </section>
        ${renderContentPreview(skill)}
      </div>
    `;
  }

  function renderSkillCard(skill) {
    const id = skillId(skill);
    const label = detectLabel(skill);
    const isSelected = state.selected.has(id);
    const isExpanded = state.expanded.has(id);
    const confidence = String(skill?.extraction?.confidence || "").trim();
    const missing = Array.isArray(skill?.extraction?.missing) ? skill.extraction.missing : [];
    const description = skill.description || t("No description reported.");

    return `
      <article class="skill-card ${isSelected ? "selected" : ""} ${label !== "not_detected" ? `skill-card-${labelClass(label)}` : ""}" data-skill-id="${escapeHtml(id)}">
        <div class="skill-card-main">
          <label class="skill-select-box" aria-label="${escapeHtml(t(`Select skill ${skill.name || ""}`))}">
            <input type="checkbox" data-action="select" data-skill-id="${escapeHtml(id)}" ${isSelected ? "checked" : ""}>
            <span></span>
          </label>
          <div class="skill-card-body">
            <div class="skill-card-title-row">
              <div class="skill-card-title-copy">
                <h3>${escapeHtml(skill.name || t("Unnamed skill"))}</h3>
                <p class="subtle">${escapeHtml(description)}</p>
              </div>
              <button class="btn skill-detail-toggle" type="button" data-action="toggle" data-skill-id="${escapeHtml(id)}">
                ${escapeHtml(isExpanded ? t("Hide skill content") : t("View skill content"))}
              </button>
            </div>
            <div class="pill-row skill-meta-row">
              <span class="pill">${escapeHtml(skill.source_framework || t("unknown framework"))}</span>
              <span class="pill">${escapeHtml(t(`${Number(skill.file_count || 0)} files`))}</span>
              <span class="pill">${formatBytes(skill.total_size)}</span>
              ${confidence ? `<span class="pill">${escapeHtml(t("confidence"))}:${escapeHtml(confidence)}</span>` : ""}
              ${missing.length ? `<span class="pill warn">${escapeHtml(t("missing"))}:${escapeHtml(missing.join(", "))}</span>` : ""}
            </div>
            ${renderResultBanner(skill)}
          </div>
        </div>
        ${isExpanded ? renderSkillDetails(skill) : ""}
      </article>
    `;
  }

  function renderSkillList() {
    renderMetrics();
    renderSelection();
    renderStatus();

    if (!state.selectedAgentId) {
      skillList.innerHTML = `<div class="empty-state">${escapeHtml(t("Choose an agent first to inspect skills."))}</div>`;
      return;
    }
    if (!state.skills.length) {
      skillList.innerHTML = `<div class="empty-state">${escapeHtml(t("No skills have been reported for this agent yet. Run an adapter or the test agent fixture first."))}</div>`;
      return;
    }
    skillList.innerHTML = `${renderDetectionError()}${sortedSkills().map(renderSkillCard).join("")}`;
  }

  function renderDetectionError() {
    if (!state.detectionError) {
      return "";
    }
    return `
      <div class="skill-error-banner" role="alert">
        <strong>${escapeHtml(t("Skill detection failed."))}</strong>
        <span>${escapeHtml(state.detectionError)}</span>
      </div>
    `;
  }

  function pruneSelection() {
    const ids = new Set(state.skills.map(skillId));
    [...state.selected].forEach((id) => {
      if (!ids.has(id)) {
        state.selected.delete(id);
      }
    });
    [...state.expanded].forEach((id) => {
      if (!ids.has(id)) {
        state.expanded.delete(id);
      }
    });
  }

  function mergeSkills(freshSkills, cachedSkills) {
    const cachedById = new Map((cachedSkills || []).map((skill) => [skillId(skill), skill]));
    return (freshSkills || []).map((fresh) => {
      const id = skillId(fresh);
      const cached = cachedById.get(id);
      if (!cached) {
        return fresh;
      }
      const freshFiles = filesForSkill(fresh);
      const cachedFiles = filesForSkill(cached);
      return {
        ...cached,
        ...fresh,
        detect_result: fresh.detect_result || null,
        skill_resource: {
          ...(cached.skill_resource || {}),
          ...(fresh.skill_resource || {}),
          files: freshFiles.length ? freshFiles : cachedFiles,
          skill_markdown: fresh.skill_resource?.skill_markdown || cached.skill_resource?.skill_markdown || null,
        },
      };
    });
  }

  function findSkill(id) {
    return state.skills.find((skill) => skillId(skill) === id) || null;
  }

  function sanitizeFileName(value, fallback = "skill") {
    return String(value || fallback)
      .replace(/[<>:"/\\|?*\x00-\x1F]/g, "_")
      .replace(/\s+/g, "_")
      .replace(/^_+|_+$/g, "")
      .slice(0, 120) || fallback;
  }

  function downloadSkill(skill) {
    const files = filesForSkill(skill);
    if (!files.length) {
      showToast(t("No files are available to download for this skill."), "warning");
      return;
    }
    const zipEntries = [];
    const notices = [];
    files.forEach((file) => {
      const relativePath = String(file.relative_path || "").trim();
      if (!relativePath) {
        return;
      }
      if (typeof file.content === "string" && !file.binary) {
        zipEntries.push({ path: relativePath, content: file.content });
      } else {
        notices.push(`${relativePath}: ${file.binary ? "binary file" : file.content_omitted || "content not reported"}`);
      }
    });
    if (notices.length) {
      zipEntries.push({
        path: "AGENTGUARD_DOWNLOAD_NOTICE.txt",
        content: [
          "Some files could not be exported because their content was not reported by the adapter.",
          "",
          ...notices,
          "",
        ].join("\n"),
      });
    }
    if (!zipEntries.length) {
      showToast(t("No previewable files are available to download for this skill."), "warning");
      return;
    }
    const blob = buildZipBlob(zipEntries);
    const link = document.createElement("a");
    const objectUrl = URL.createObjectURL(blob);
    link.href = objectUrl;
    link.download = `${sanitizeFileName(skill.name || skillId(skill))}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(objectUrl);
  }

  function buildZipBlob(entries) {
    const encoder = new TextEncoder();
    const chunks = [];
    const central = [];
    let offset = 0;
    entries.forEach((entry) => {
      const nameBytes = encoder.encode(safeZipPath(entry.path));
      const data = encoder.encode(String(entry.content ?? ""));
      const crc = crc32(data);
      const local = new Uint8Array(30 + nameBytes.length);
      const view = new DataView(local.buffer);
      view.setUint32(0, 0x04034b50, true);
      view.setUint16(4, 20, true);
      view.setUint16(8, 0, true);
      view.setUint16(10, 0, true);
      view.setUint32(14, crc, true);
      view.setUint32(18, data.length, true);
      view.setUint32(22, data.length, true);
      view.setUint16(26, nameBytes.length, true);
      local.set(nameBytes, 30);
      chunks.push(local, data);

      const header = new Uint8Array(46 + nameBytes.length);
      const centralView = new DataView(header.buffer);
      centralView.setUint32(0, 0x02014b50, true);
      centralView.setUint16(4, 20, true);
      centralView.setUint16(6, 20, true);
      centralView.setUint16(10, 0, true);
      centralView.setUint16(12, 0, true);
      centralView.setUint32(16, crc, true);
      centralView.setUint32(20, data.length, true);
      centralView.setUint32(24, data.length, true);
      centralView.setUint16(28, nameBytes.length, true);
      centralView.setUint32(42, offset, true);
      header.set(nameBytes, 46);
      central.push(header);
      offset += local.length + data.length;
    });
    const centralSize = central.reduce((sum, chunk) => sum + chunk.length, 0);
    const end = new Uint8Array(22);
    const endView = new DataView(end.buffer);
    endView.setUint32(0, 0x06054b50, true);
    endView.setUint16(8, entries.length, true);
    endView.setUint16(10, entries.length, true);
    endView.setUint32(12, centralSize, true);
    endView.setUint32(16, offset, true);
    return new Blob([...chunks, ...central, end], { type: "application/zip" });
  }

  function safeZipPath(value) {
    const parts = String(value || "file.txt")
      .replace(/\\/g, "/")
      .split("/")
      .filter((part) => part && part !== "." && part !== "..")
      .map((part) => sanitizeFileName(part, "file"));
    return parts.join("/") || "file.txt";
  }

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc = (crc >>> 8) ^ CRC32_TABLE[(crc ^ byte) & 0xff];
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  const CRC32_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let i = 0; i < 256; i += 1) {
      let value = i;
      for (let bit = 0; bit < 8; bit += 1) {
        value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
      }
      table[i] = value >>> 0;
    }
    return table;
  })();

  async function loadSkills({ manual = false } = {}) {
    if (!state.selectedAgentId) {
      state.skills = [];
      renderSkillList();
      return;
    }

    const cachedBeforeRefresh = data.loadSkillList(state.selectedAgentId);
    if (!state.skills.length && cachedBeforeRefresh.length) {
      state.skills = withoutDetectionResults(cachedBeforeRefresh);
    }

    state.loading = true;
    refreshButton.disabled = true;
    if (!state.detecting) {
      state.detectionError = "";
    }
    renderStatus(manual ? "Refreshing skill catalog..." : "Loading skill catalog...");
    renderSkillList();
    try {
      const freshSkills = await data.refreshSkillList(state.selectedAgentId);
      state.skills = mergeSkills(freshSkills, cachedBeforeRefresh);
      data.persistSkillList(state.skills, state.selectedAgentId);
      pruneSelection();
      renderSkillList();
      if (manual) {
        showToast(t("Skill catalog refreshed."), "success");
      }
    } catch (error) {
      state.skills = cachedBeforeRefresh.length ? cachedBeforeRefresh : data.loadSkillList(state.selectedAgentId);
      pruneSelection();
      renderSkillList();
      const cachedAt = data.getLastSkillSyncTime?.();
      renderStatus(cachedAt
        ? `Showing cached skill catalog. Last successful sync: ${cachedAt}`
        : api.formatErrorMessage(error, "Failed to load skill catalog."));
      showToast(t(api.formatErrorMessage(error, "Failed to load skill catalog.")), "warning");
    } finally {
      state.loading = false;
      refreshButton.disabled = false;
      renderSkillList();
    }
  }

  async function detectSelectedSkills() {
    if (state.detecting) {
      return;
    }
    const ids = [...state.selected];
    if (!ids.length || !state.selectedAgentId) {
      showToast(t("Select at least one skill first."), "warning");
      return;
    }
    state.detecting = true;
    state.pendingUseLlm = useLlmInput?.checked === true;
    state.pendingSkillIds = new Set(ids);
    state.pendingRuleIds = new Set(ids);
    state.pendingLlmIds = new Set();
    state.waitingLlmIds = new Set(state.pendingUseLlm ? ids : []);
    state.detectionError = "";
    state.skills = state.skills.map((skill) => (
      state.pendingSkillIds.has(skillId(skill))
        ? { ...skill, detect_result: null }
        : skill
    ));
    startDetectTimer();
    renderSkillList();
    try {
      const ruleResults = await runDetectionQueue(ids, {
        concurrency: Math.min(8, ids.length),
        useLlm: false,
        phase: "rule",
      });
      const detected = ruleResults.filter((result) => result.ok).length;
      const llmIds = ruleResults.filter((result) => result.ok).map((result) => result.id);
      if (state.pendingUseLlm && llmIds.length) {
        await runDetectionQueue(llmIds, {
          concurrency: Math.min(clampConcurrency(llmConcurrencyInput?.value), ids.length),
          useLlm: true,
          phase: "llm",
        });
      }
      showToast(t(`Detected ${detected} ${plural(detected, "skill", "skills")}.`), "success");
    } catch (error) {
      state.detectionError = api.formatErrorMessage(error, "Skill detection failed.");
      showToast(t(state.detectionError), "warning");
    } finally {
      state.detecting = false;
      state.pendingSkillIds.clear();
      state.pendingRuleIds.clear();
      state.pendingLlmIds.clear();
      state.waitingLlmIds.clear();
      state.pendingUseLlm = false;
      stopDetectTimer();
      renderSkillList();
    }
  }

  async function runDetectionQueue(ids, { concurrency, useLlm, phase }) {
    const queue = ids.slice();
    const results = [];
    const workerCount = Math.max(1, Math.min(concurrency || 1, queue.length || 1));
    async function worker() {
      while (queue.length) {
        const id = queue.shift();
        if (!id) {
          continue;
        }
        if (phase === "llm") {
          state.waitingLlmIds.delete(id);
          state.pendingLlmIds.add(id);
        }
        renderSkillList();
        let ok = false;
        try {
          const payload = await data.detectSkills(state.selectedAgentId, [id], {
            useLlm,
            llmConcurrency: concurrency,
            timeoutMs: 80000,
          });
          applyDetectPayload(payload);
          results.push({ id, ok: true, payload });
          ok = true;
        } catch (error) {
          state.detectionError = api.formatErrorMessage(error, "Skill detection failed.");
          results.push({ id, ok: false, error });
          showToast(t(`${findSkill(id)?.name || id}: ${state.detectionError}`), "warning");
        } finally {
          if (phase === "rule") {
            state.pendingRuleIds.delete(id);
            if (!state.pendingUseLlm || !ok) {
              state.waitingLlmIds.delete(id);
              state.pendingSkillIds.delete(id);
            }
          } else {
            state.pendingLlmIds.delete(id);
            state.waitingLlmIds.delete(id);
            state.pendingSkillIds.delete(id);
          }
          renderSkillList();
        }
      }
    }
    await Promise.all(Array.from({ length: workerCount }, () => worker()));
    return results;
  }

  function applyDetectPayload(result) {
    const updates = new Map();
    (result?.results || []).forEach((item) => {
      const nextSkill = item.skill || null;
      if (nextSkill?.skill_unique_id) {
        updates.set(nextSkill.skill_unique_id, nextSkill);
      } else if (item.skill_unique_id) {
        updates.set(item.skill_unique_id, {
          ...(state.skills.find((skill) => skill.skill_unique_id === item.skill_unique_id) || {}),
          detect_result: item.detect_result,
        });
      }
    });
    if (!updates.size) {
      return;
    }
    state.skills = state.skills.map((skill) => updates.get(skill.skill_unique_id) || skill);
    data.persistSkillList(state.skills, state.selectedAgentId);
  }

  function startDetectTimer() {
    stopDetectTimer();
    state.detectStartedAt = Date.now();
    state.detectElapsedS = 0;
    state.detectTimer = window.setInterval(() => {
      state.detectElapsedS = Math.floor((Date.now() - state.detectStartedAt) / 1000);
      renderSkillList();
    }, 1000);
  }

  function stopDetectTimer() {
    if (state.detectTimer) {
      window.clearInterval(state.detectTimer);
      state.detectTimer = null;
    }
  }

  if (llmConcurrencyInput) {
    llmConcurrencyInput.value = String(clampConcurrency(llmConcurrencyInput.value));
  }

  refreshButton?.addEventListener("click", () => {
    loadSkills({ manual: true });
  });

  selectAllButton?.addEventListener("click", () => {
    state.skills.forEach((skill) => {
      const id = skillId(skill);
      if (id) {
        state.selected.add(id);
      }
    });
    renderSkillList();
  });

  clearSelectionButton?.addEventListener("click", () => {
    const selected = state.selected.size;
    state.selected.clear();
    renderSkillList();
    if (selected > 0) {
      showToast(t("Skill selection cleared."), "success");
    }
  });

  detectButton?.addEventListener("click", detectSelectedSkills);

  skillList?.addEventListener("change", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.dataset.action !== "select") {
      return;
    }
    const id = String(target.dataset.skillId || "").trim();
    if (!id) {
      return;
    }
    if (target.checked) {
      state.selected.add(id);
    } else {
      state.selected.delete(id);
    }
    renderSkillList();
  });

  skillList?.addEventListener("click", (event) => {
    const button = event.target instanceof Element ? event.target.closest("[data-action]") : null;
    if (!button) {
      return;
    }
    const action = String(button.dataset.action || "").trim();
    const id = String(button.dataset.skillId || "").trim();
    if (!id) {
      return;
    }
    if (action === "select-file") {
      state.selectedFiles.set(id, String(button.dataset.filePath || "").trim());
      renderSkillList();
      return;
    }
    if (action === "download-skill") {
      const skill = findSkill(id);
      if (skill) {
        downloadSkill(skill);
      }
      return;
    }
    if (action !== "toggle") {
      return;
    }
    if (state.expanded.has(id)) {
      state.expanded.delete(id);
    } else {
      state.expanded.add(id);
    }
    renderSkillList();
  });

  window.addEventListener("agentguard:selected-agent-change", (event) => {
    state.selectedAgentId = String(event?.detail?.agentId || "").trim();
    state.skills = withoutDetectionResults(data.loadSkillList(state.selectedAgentId));
    state.selected.clear();
    state.expanded.clear();
    state.pendingSkillIds.clear();
    state.detectionError = "";
    loadSkills();
  });

  state.skills = withoutDetectionResults(data.loadSkillList(state.selectedAgentId));
  renderSkillList();
  loadSkills();
})();
