(function () {
  function normalizeCatalog(catalog) {
    return Array.isArray(catalog) ? catalog : [];
  }

  function toolDisplayName(tool, catalog = []) {
    if (!tool) {
      return "";
    }
    const normalizedCatalog = normalizeCatalog(catalog);
    const duplicates = normalizedCatalog.filter((item) => item?.name === tool.name);
    return duplicates.length > 1
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
    normalizedCatalog.sort((a, b) => toolDisplayName(a, normalizedCatalog).localeCompare(toolDisplayName(b, normalizedCatalog)));
    return normalizedCatalog;
  }

  function toToolOptions(catalog) {
    const normalizedCatalog = sortCatalogByDisplayName(catalog);
    return normalizedCatalog
      .filter((tool) => String(tool?.tool_key || "").trim())
      .map((tool) => ({
        value: tool.tool_key,
        label: toolDisplayName(tool, normalizedCatalog),
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
