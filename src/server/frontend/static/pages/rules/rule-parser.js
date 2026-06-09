(function () {
  function parseConditionValue(rawValue) {
    const value = String(rawValue || "").trim();
    const quoted = value.match(/^"(.*)"$/);
    if (!quoted) {
      return value;
    }
    return quoted[1]
      .replace(/\\"/g, "\"")
      .replace(/\\\\/g, "\\");
  }

  function extractRuleMetadata(text) {
    const source = String(text || "").trim();
    const onClause = source.match(/^ON(?::\s*|\s+)(.+)$/m)?.[1]?.trim() || "";
    const severity = source.match(/^Severity:\s+(.+)$/m)?.[1]?.trim() || "";
    const category = source.match(/^Category:\s+(.+)$/m)?.[1]?.trim() || "";
    const reasonRaw = source.match(/^Reason:\s+(.+)$/m)?.[1]?.trim() || "";
    const promptRaw = source.match(/^Prompt:\s+(.+)$/m)?.[1]?.trim() || "";
    const reason = reasonRaw ? parseConditionValue(reasonRaw) : "";
    const prompt = promptRaw ? parseConditionValue(promptRaw) : "";
    return { onClause, severity, category, reason, prompt };
  }

  function parseConditionExpression(expression) {
    const trimmed = String(expression || "").trim();
    if (!trimmed) {
      return null;
    }

    const leadingParens = trimmed.match(/^\(+/);
    const trailingParens = trimmed.match(/\)+$/);
    const openParen = leadingParens ? leadingParens[0] : "";
    const closeParen = trailingParens ? trailingParens[0] : "";
    const core = trimmed.slice(openParen.length, trimmed.length - closeParen.length).trim();

    const parsed = core.match(
      /^([A-Z])\.(name|boundary|sensitivity|integrity|label\.boundary|label\.sensitivity|label\.integrity|syntax\.([A-Za-z0-9_]+)|([A-Za-z0-9_]+))\s+(==|!=|>=|<=|>|<|CONTAINS)\s+(.+)$/,
    );
    if (parsed) {
      const [, symbol, featurePath, legacySyntaxField = "", inferredSyntaxField = "", operator, rawValue] = parsed;
      let feature = featurePath;
      let syntaxField = legacySyntaxField || inferredSyntaxField || "";

      if (featurePath === "boundary" || featurePath === "sensitivity" || featurePath === "integrity") {
        feature = `label.${featurePath}`;
        syntaxField = "";
      } else if (featurePath.startsWith("syntax.")) {
        feature = "syntax";
      } else if (featurePath !== "name" && featurePath !== "label.boundary" && featurePath !== "label.sensitivity" && featurePath !== "label.integrity") {
        feature = "syntax";
      }

      return {
        confirmed: true,
        connector: "",
        openParen,
        closeParen,
        sourceType: "trace",
        symbol,
        feature,
        syntaxField,
        operator: operator === "CONTAINS" ? "contains" : operator,
        value: parseConditionValue(rawValue),
        selectedToolKey: "",
        contextPrefix: "",
        contextField: "",
        contextFieldName: "",
        contextPath: "",
      };
    }

    const contextParsed = core.match(
      /^((?:tool|target|principal|caller|event)\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)\s+(==|!=|>=|<=|>|<|CONTAINS)\s+(.+)$/,
    );
    if (!contextParsed) {
      return null;
    }

    const [, contextPath, operator, rawValue] = contextParsed;
    const parts = contextPath.split(".");
    const contextPrefix = parts[0];
    let contextField = contextPath;
    let contextFieldName = "";
    let syntaxField = "";

    if (contextPrefix === "tool") {
      if (parts[1] && ["name", "boundary", "sensitivity", "integrity"].includes(parts[1])) {
        contextField = `tool.${parts[1]}`;
      } else if (parts[1]) {
        contextField = "tool.syntax";
        syntaxField = parts.slice(1).join(".");
      }
    } else if (contextPrefix === "target") {
      if (parts[1] === "domain") {
        contextField = "target.domain";
      } else {
        contextField = "target.raw";
        contextFieldName = parts.slice(1).join(".");
      }
    }

    return {
      confirmed: true,
      connector: "",
      openParen,
      closeParen,
      sourceType: "context",
      symbol: "",
      feature: "",
      syntaxField,
      operator: operator === "CONTAINS" ? "contains" : operator,
      value: parseConditionValue(rawValue),
      selectedToolKey: "",
      contextPrefix,
      contextField,
      contextFieldName,
      contextPath,
    };
  }

  function parseConditionItems(conditionBlock) {
    const source = String(conditionBlock || "").trim();
    if (!source) {
      return [];
    }

    return source
      .split(/\n/)
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line, index) => {
        const matched = line.match(/^(AND|OR)\s+(.+)$/);
        const connector = index === 0 ? "" : (matched ? matched[1] : "AND");
        const expression = index === 0 ? line : (matched ? matched[2] : line);
        const item = parseConditionExpression(expression);
        if (!item) {
          return null;
        }
        return {
          ...item,
          connector,
        };
      })
      .filter(Boolean);
  }

  function splitRuleBlocks(source) {
    const text = String(source || "").trim();
    if (!text) {
      return [];
    }

    const matches = text.match(/(?:^|\n)(RULE(?::\s*|\s+)[A-Za-z_][A-Za-z0-9_-]*[\s\S]*?)(?=\nRULE(?::\s*|\s+)[A-Za-z_][A-Za-z0-9_-]*|\s*$)/g);
    if (!matches) {
      return [];
    }

    return matches.map((block) => String(block || "").trim()).filter(Boolean);
  }

  function extractPublishedRuleSource(source, ruleName = "") {
    const blocks = splitRuleBlocks(source);
    if (!blocks.length) {
      return String(source || "").trim();
    }

    const normalizedRuleName = String(ruleName || "").trim();
    if (!normalizedRuleName) {
      return blocks[0];
    }

    const escapedRuleName = normalizedRuleName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const expectedHeader = new RegExp(`^RULE(?::\\s*|\\s+)${escapedRuleName}$`, "m");
    return blocks.find((block) => expectedHeader.test(block)) || blocks[0];
  }

  function parsePublishedRuleSource(source, normalizeRule, publishedStatus) {
    const text = String(source || "").trim();
    if (!text) {
      return null;
    }

    const ruleName = text.match(/^RULE(?::\s*|\s+)([A-Za-z_][A-Za-z0-9_-]*)$/m)?.[1] || "";
    const path = text.match(/^TRACE:\s+(.+)$/m)?.[1] || "";
    const metadata = extractRuleMetadata(text);
    const actionLine = text.match(/^POLICY:\s+([A-Z_]+)(?:\(([^)]*)\)|\s+TO\s+"([^"]+)")?/m);
    const conditionMatch = text.match(/^CONDITION:\s+([\s\S]*?)\nPOLICY:/m);
    const conditionItems = parseConditionItems(conditionMatch?.[1] || "");

    if (!ruleName || (!path && !metadata.onClause) || !actionLine || !conditionItems.length) {
      return null;
    }

    const rule = {
      name: ruleName,
      status: publishedStatus,
      entryMode: metadata.onClause ? "on" : "trace",
      path,
      onClause: metadata.onClause,
      conditionItems,
      action: actionLine[1],
      degradeTarget: actionLine[3] || actionLine[2] || "",
      severity: metadata.severity,
      category: metadata.category,
      reason: metadata.reason,
      prompt: metadata.prompt,
      description: "",
      source: text,
    };

    return typeof normalizeRule === "function" ? normalizeRule(rule) : rule;
  }

  window.AgentGuardRuleParser = {
    extractPublishedRuleSource,
    extractRuleMetadata,
    parseConditionExpression,
    parseConditionItems,
    parseConditionValue,
    parsePublishedRuleSource,
    splitRuleBlocks,
  };
})();
