(function () {
  function normalizeCatalog(catalog) {
    return Array.isArray(catalog) ? catalog : [];
  }

  function buildNameCounts(catalog = []) {
    const counts = new Map();
    normalizeCatalog(catalog).forEach((item) => {
      const name = String(item?.name || "").trim();
      if (!name) {
        return;
      }
      counts.set(name, (counts.get(name) || 0) + 1);
    });
    return counts;
  }

  function toolDisplayName(tool, catalog = [], nameCounts = null) {
    if (!tool) {
      return "";
    }
    if (String(tool?.source_type || "").trim() === "mcp") {
      const serverName = String(tool?.mcp_name || "").trim();
      const toolName = String(tool?.mcp_tool_name || tool?.name || "").trim();
      return `${serverName && toolName ? `${serverName} / ${toolName}` : String(tool.name || "").trim()} [MCP]`;
    }
    const counts = nameCounts instanceof Map ? nameCounts : buildNameCounts(catalog);
    const duplicateCount = counts.get(String(tool.name || "").trim()) || 0;
    return duplicateCount > 1
      ? `${tool.owner_agent_id} / ${tool.name}`
      : String(tool.name || "").trim();
  }

  function toolKeyForName(toolName, catalog = []) {
    const normalizedName = String(toolName || "").trim();
    if (!normalizedName) {
      return "";
    }
    const match = normalizeCatalog(catalog).find((tool) => tool?.name === normalizedName);
    return String(match?.tool_key || "").trim();
  }

  function toolNameForKey(toolKey, catalog = [], findToolByKey) {
    const normalizedKey = String(toolKey || "").trim();
    if (!normalizedKey) {
      return "";
    }
    if (typeof findToolByKey === "function") {
      const match = findToolByKey(normalizeCatalog(catalog), normalizedKey);
      return match ? String(match.name || "").trim() : "";
    }
    const fallback = normalizeCatalog(catalog).find((tool) => String(tool?.tool_key || "").trim() === normalizedKey);
    return fallback ? String(fallback.name || "").trim() : "";
  }

  function sortCatalogByDisplayName(catalog) {
    const normalizedCatalog = normalizeCatalog(catalog).slice();
    const nameCounts = buildNameCounts(normalizedCatalog);
    normalizedCatalog.sort((a, b) => toolDisplayName(a, normalizedCatalog, nameCounts).localeCompare(toolDisplayName(b, normalizedCatalog, nameCounts)));
    return normalizedCatalog;
  }

  function toToolOptions(catalog) {
    const normalizedCatalog = sortCatalogByDisplayName(catalog);
    const nameCounts = buildNameCounts(normalizedCatalog);
    return normalizedCatalog
      .filter((tool) => String(tool?.tool_key || "").trim())
      .map((tool) => ({
        value: tool.tool_key,
        label: toolDisplayName(tool, normalizedCatalog, nameCounts),
        name: String(tool.name || "").trim(),
      }));
  }

  window.AgentGuardToolCatalog = {
    normalizeCatalog,
    sortCatalogByDisplayName,
    toToolOptions,
    toolDisplayName,
    toolKeyForName,
    toolNameForKey,
  };
})();
