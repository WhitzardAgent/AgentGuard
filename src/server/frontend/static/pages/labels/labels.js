const labelOptions = {
  boundary: ["internal", "external", "privileged"],
  sensitivity: ["low", "moderate", "high"],
  integrity: ["trusted", "unfiltered"],
};

const toolData = window.AgentGuardData;
const shell = window.AgentGuardShell;
const api = window.AgentGuardApi;

const toolSelect = document.getElementById("tool-select");
const addLabelRowButton = document.getElementById("add-label-row");
const labelConfigList = document.getElementById("label-config-list");
const pendingLabelPreview = document.getElementById("pending-label-preview");
const pendingLabelTitle = document.getElementById("pending-label-title");
const saveToolLabelButton = document.getElementById("save-tool-label");
const resetToolLabelButton = document.getElementById("reset-tool-label");
const configuredToolLabelsBody = document.getElementById("configured-tool-labels-body");
const refreshToolsButton = document.getElementById("refresh-tools");
const toolSyncStatus = document.getElementById("tool-sync-status");

let toolCatalog = [];
let labelRows = [];
let selectedAgentId = shell?.getState?.().selectedAgentId || "";

shell?.setPageContext({
  title: "Tool Labels",
  description: "Inspect the tool catalog, tune label values, and keep the shared label surface clean.",
});

function getToolMeta(toolKey) {
  return toolData?.findToolByKey?.(toolCatalog, toolKey) || null;
}

function toolDisplayName(tool) {
  if (!tool) {
    return "";
  }
  const duplicates = toolCatalog.filter((item) => item.name === tool.name);
  return duplicates.length > 1
    ? `${tool.owner_agent_id} / ${tool.name}`
    : tool.name;
}

function updateSyncStatus(message) {
  if (toolSyncStatus) {
    toolSyncStatus.textContent = message;
  }
}

function renderNoAgentState() {
  toolCatalog = [];
  toolSelect.innerHTML = "";
  configuredToolLabelsBody.innerHTML = "";
  labelRows = [];
  renderLabelRows();
  updateSyncStatus("Choose an agent first to load that agent's tools.");
  shell?.setToolStatus("Waiting for agent selection");
}

function renderToolOptions() {
  const currentValue = toolSelect.value;
  toolSelect.innerHTML = "";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Select a tool";
  placeholder.disabled = true;
  placeholder.hidden = true;
  placeholder.selected = !currentValue;
  toolSelect.appendChild(placeholder);

  toolCatalog
    .slice()
    .sort((a, b) => toolDisplayName(a).localeCompare(toolDisplayName(b)))
    .forEach((tool) => {
      const option = document.createElement("option");
      option.value = tool.tool_key;
      option.textContent = toolDisplayName(tool);
      option.selected = currentValue === tool.tool_key;
      toolSelect.appendChild(option);
    });
}

function availableCategories(currentIndex) {
  const used = new Set(
    labelRows
      .filter((row, index) => index !== currentIndex && row.category)
      .map((row) => row.category),
  );

  return Object.keys(labelOptions).filter((category) => !used.has(category));
}

function updatePreview() {
  pendingLabelPreview.innerHTML = "";
  const tool = getToolMeta(toolSelect.value);
  pendingLabelTitle.textContent = tool
    ? `Labels to write for ${toolDisplayName(tool)}:`
    : "Labels to write:";

  const completeRows = labelRows.filter((row) => row.category && row.value);
  if (!tool) {
    const empty = document.createElement("span");
    empty.className = "subtle";
    empty.textContent = "Select a tool first.";
    pendingLabelPreview.appendChild(empty);
    return;
  }

  if (!completeRows.length) {
    const empty = document.createElement("span");
    empty.className = "subtle";
    empty.textContent = "No label rows selected yet. Click + to add one.";
    pendingLabelPreview.appendChild(empty);
    return;
  }

  completeRows.forEach((row) => {
    const pill = document.createElement("span");
    pill.className = row.category === "sensitivity" ? "pill warn" : "pill";
    if (row.category === "boundary" && row.value === "privileged") {
      pill.className = "pill danger";
    }
    pill.textContent = `${row.category}:${row.value}`;
    pendingLabelPreview.appendChild(pill);
  });

}

function renderConfiguredToolLabels() {
  configuredToolLabelsBody.innerHTML = "";

  toolCatalog.forEach((tool) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${tool.owner_agent_id || "-"}</td>
      <td>${tool.name}</td>
      <td>${tool.labels.boundary || "-"}</td>
      <td>${tool.labels.sensitivity || "-"}</td>
      <td>${tool.labels.integrity || "-"}</td>
    `;
    configuredToolLabelsBody.appendChild(tr);
  });
}

function currentSelection() {
  const output = {
    tool_key: toolSelect.value,
    boundary: "",
    sensitivity: "",
    integrity: "",
  };

  labelRows.forEach((row) => {
    if (row.category && row.value) {
      output[row.category] = row.value;
    }
  });

  return output;
}

function loadToolSelection(tool) {
  const meta = getToolMeta(tool);
  if (!meta) {
    labelRows = [];
    renderLabelRows();
    return;
  }

  labelRows = [];
  ["boundary", "sensitivity", "integrity"].forEach((category) => {
    if (meta.labels[category]) {
      labelRows.push({ category, value: meta.labels[category] });
    }
  });
  renderLabelRows();
}

function resetEditor() {
  loadToolSelection(toolSelect.value);
}

function showToast(message, tone) {
  window.AgentGuardUI?.showToast?.(message, tone);
}

function createOption({ value, text, selected = false, disabled = false, hidden = false }) {
  const option = document.createElement("option");
  option.value = value;
  option.textContent = text;
  option.selected = selected;
  option.disabled = disabled;
  option.hidden = hidden;
  return option;
}

function createField(child) {
  const field = document.createElement("div");
  field.className = "field";
  field.appendChild(child);
  return field;
}

function renderEmptyLabelRows() {
  const empty = document.createElement("div");
  empty.className = "empty-state";
  empty.textContent = toolSelect.value
    ? "Click + to add a label row."
    : "Select a tool, then click + to add a label row.";
  labelConfigList.appendChild(empty);
}

function createCategorySelect(row, index) {
  const categorySelect = document.createElement("select");

  categorySelect.appendChild(createOption({
    value: "",
    text: "Select category",
    selected: !row.category,
    disabled: true,
    hidden: true,
  }));

  availableCategories(index).forEach((category) => {
    categorySelect.appendChild(createOption({
      value: category,
      text: category,
      selected: row.category === category,
    }));
  });

  categorySelect.addEventListener("change", (event) => {
    labelRows[index].category = event.target.value;
    labelRows[index].value = "";
    renderLabelRows();
  });

  return categorySelect;
}

function createValueSelect(row, index) {
  const valueSelect = document.createElement("select");
  valueSelect.disabled = !row.category;

  valueSelect.appendChild(createOption({
    value: "",
    text: row.category ? "Select value" : "Select category first",
    selected: !row.value,
    disabled: true,
    hidden: true,
  }));

  if (row.category) {
    labelOptions[row.category].forEach((value) => {
      valueSelect.appendChild(createOption({
        value,
        text: value,
        selected: row.value === value,
      }));
    });
  }

  valueSelect.addEventListener("change", (event) => {
    labelRows[index].value = event.target.value;
    updatePreview();
  });

  return valueSelect;
}

function createRemoveButton(index) {
  const removeButton = document.createElement("button");
  removeButton.className = "btn";
  removeButton.type = "button";
  removeButton.textContent = "Remove";

  removeButton.addEventListener("click", () => {
    labelRows.splice(index, 1);
    renderLabelRows();
  });

  return removeButton;
}

function createLabelRow(row, index) {
  const wrap = document.createElement("div");
  wrap.className = "label-config-row";

  wrap.appendChild(createField(createCategorySelect(row, index)));
  wrap.appendChild(createField(createValueSelect(row, index)));
  wrap.appendChild(createRemoveButton(index));

  return wrap;
}

function renderLabelRows() {
  labelConfigList.innerHTML = "";

  if (!labelRows.length) {
    renderEmptyLabelRows();
    updatePreview();
    return;
  }

  labelRows.forEach((row, index) => {
    labelConfigList.appendChild(createLabelRow(row, index));
  });

  updatePreview();
}

async function refreshToolCatalog({ manual = false } = {}) {
  if (!selectedAgentId) {
    renderNoAgentState();
    if (manual) {
      showToast("Choose an agent first.", "warning");
    }
    return;
  }
  refreshToolsButton.disabled = true;
  updateSyncStatus(manual ? `Refreshing ${selectedAgentId} tools...` : `Syncing ${selectedAgentId} tools...`);

  try {
    toolCatalog = await toolData.refreshToolCatalog(selectedAgentId);
    renderToolOptions();
    renderConfiguredToolLabels();

    if (toolSelect.value) {
      loadToolSelection(toolSelect.value);
    }

    const syncedAt = toolData.getLastToolSyncTime();
    updateSyncStatus(`Synced ${toolCatalog.length} tools for ${selectedAgentId}. Last updated: ${syncedAt || "just now"}`);
    shell?.setToolStatus(syncedAt ? `Last synced ${syncedAt}` : "Synced just now");
    if (manual) {
      showToast("Tool catalog refreshed.", "success");
    }
  } catch (error) {
    const cachedAt = toolData.getLastToolSyncTime();
    if (cachedAt) {
      updateSyncStatus(`Showing cached tool catalog. Last successful sync: ${cachedAt}`);
      shell?.setToolStatus(`Cached data from ${cachedAt}`);
    } else {
      updateSyncStatus("Showing the built-in empty tool catalog fallback.");
      shell?.setToolStatus("No sync yet");
    }
    showToast(api.formatErrorMessage(error, "Failed to refresh tool catalog."), "warning");
  } finally {
    refreshToolsButton.disabled = false;
  }
}

addLabelRowButton.addEventListener("click", () => {
  if (!selectedAgentId) {
    showToast("Choose an agent first.", "warning");
    return;
  }
  if (!toolSelect.value) {
    showToast("Select a tool first.", "warning");
    return;
  }
  if (labelRows.length >= Object.keys(labelOptions).length) {
    return;
  }
  labelRows.push({ category: "", value: "" });
  renderLabelRows();
});

saveToolLabelButton.addEventListener("click", async () => {
  const selection = currentSelection();
  if (!selection.tool_key) {
    showToast("Select a tool first.", "warning");
    return;
  }

  const hasAnyLabel = selection.boundary || selection.sensitivity || selection.integrity;
  if (!hasAnyLabel) {
    const selectedTool = getToolMeta(selection.tool_key);
    showToast(`Select at least one label for ${toolDisplayName(selectedTool)}.`, "warning");
    return;
  }

  const tool = getToolMeta(selection.tool_key);
  if (!tool || !selectedAgentId) {
    showToast("Select a valid tool first.", "warning");
    return;
  }

  saveToolLabelButton.disabled = true;
  try {
    await toolData.updateToolLabels(selectedAgentId, tool.name, {
      boundary: selection.boundary || "internal",
      sensitivity: selection.sensitivity || "low",
      integrity: selection.integrity || "trusted",
      tags: Array.isArray(tool.labels?.tags) ? tool.labels.tags : [],
    });
    await refreshToolCatalog();
    showToast(`${toolDisplayName(tool)} labels saved.`, "success");
    return;
  } catch (error) {
    showToast(api.formatErrorMessage(error, "Failed to save tool labels."), "warning");
    return;
  } finally {
    saveToolLabelButton.disabled = false;
  }

  if (tool) {
    tool.labels.boundary = selection.boundary || '-';
    tool.labels.sensitivity = selection.sensitivity || '-';
    tool.labels.integrity = selection.integrity || '-';
    toolData.persistToolCatalog(toolCatalog);
    shell?.setToolStatus(`Last synced ${toolData.getLastToolSyncTime() || "just now"}`);
    //TODO: 后端同步更新工具的labels，从而guard可以获取最新的标签
  }
  renderConfiguredToolLabels();
  showToast(`${toolDisplayName(tool)} labels saved.`, "success");
});

resetToolLabelButton.addEventListener("click", () => {
  resetEditor();
});

refreshToolsButton.addEventListener("click", () => {
  refreshToolCatalog({ manual: true });
});

toolSelect.addEventListener("change", (event) => {
  loadToolSelection(event.target.value);
});

if (typeof window !== "undefined" && typeof window.addEventListener === "function") {
  window.addEventListener("agentguard:selected-agent-change", (event) => {
    selectedAgentId = String(event?.detail?.agentId || "").trim();
    renderNoAgentState();
    if (selectedAgentId) {
      refreshToolCatalog();
    }
  });
}

toolCatalog = toolData.loadToolCatalog(selectedAgentId);
if (!selectedAgentId) {
  renderNoAgentState();
} else {
  renderToolOptions();
  renderConfiguredToolLabels();
  loadToolSelection(toolSelect.value);
  refreshToolCatalog();
}
