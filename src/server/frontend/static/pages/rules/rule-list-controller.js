(function () {
  function createRuleListController({
    ruleList,
    ruleFilterButtons,
    filterRuleItems,
    publishedStatus,
    unpublishedStatus,
    ruleDisplayName,
    ruleSourceLabel,
    actionTone,
    buildRuleListSource,
    createRuleActionButton,
    onPublishRule,
    onDeleteLocalRule,
    onDisableRule,
    onSelectRule,
  }) {
    let currentFilter = "all";

    function renderRuleListEmptyState() {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      if (currentFilter === publishedStatus) {
        empty.textContent = "There are no published runtime rules right now.";
      } else if (currentFilter === unpublishedStatus) {
        empty.textContent = "There are no unpublished local rules yet. Generate one to keep it here before publishing.";
      } else {
        empty.textContent = "There are no rules yet. Generate a local rule or publish one to the runtime.";
      }
      return empty;
    }

    function renderRuleListHeader(rule, status) {
      const header = document.createElement("div");
      header.className = "rule-list-top";

      const titleGroup = document.createElement("div");
      titleGroup.className = "rule-list-title-group";

      const title = document.createElement("strong");
      title.textContent = ruleDisplayName(rule);
      titleGroup.appendChild(title);

      const meta = document.createElement("div");
      meta.className = "rule-list-meta";

      const statusPill = document.createElement("span");
      statusPill.className = `pill ${status === publishedStatus ? "" : "warn"}`.trim();
      statusPill.textContent = status === publishedStatus ? "Published" : "Unpublished";
      meta.appendChild(statusPill);

      const actionPill = document.createElement("span");
      actionPill.className = `pill ${actionTone(String(rule.action || "").toUpperCase())}`.trim();
      actionPill.textContent = rule.action || "-";
      meta.appendChild(actionPill);

      const sourceLabel = typeof ruleSourceLabel === "function" ? String(ruleSourceLabel(rule, status) || "").trim() : "";
      if (sourceLabel) {
        const sourcePill = document.createElement("span");
        sourcePill.className = "pill";
        sourcePill.textContent = sourceLabel;
        meta.appendChild(sourcePill);
      }

      titleGroup.appendChild(meta);
      header.appendChild(titleGroup);
      return header;
    }

    function renderRuleListActions(rule, status) {
      const headerActions = document.createElement("div");
      headerActions.className = "rule-list-actions";

      const buttonGroup = document.createElement("div");
      buttonGroup.className = "rule-list-buttons";
      const userManaged = rule?.userManaged !== false;

      if (status === unpublishedStatus && userManaged) {
        buttonGroup.appendChild(createRuleActionButton("/assets/publish.png", "Publish rule", () => {
          onPublishRule(rule);
        }));
        buttonGroup.appendChild(createRuleActionButton("/assets/close.png", "Delete unpublished rule", () => {
          onDeleteLocalRule(rule);
        }));
      } else if (status === publishedStatus && userManaged) {
        buttonGroup.appendChild(createRuleActionButton("/assets/disable.png", "Disable published rule", () => {
          onDisableRule(rule);
        }));
      }

      headerActions.appendChild(buttonGroup);
      return headerActions;
    }

    function renderRuleListBody(rule, status) {
      const selectable = typeof onSelectRule === "function";
      const body = document.createElement(selectable ? "button" : "div");
      body.className = "rule-list-body";
      if (selectable) {
        body.type = "button";
      }

      const pre = document.createElement("pre");
      pre.className = "rule-list-rule";
      pre.textContent = buildRuleListSource(rule, status);
      body.appendChild(pre);

      if (selectable) {
        body.addEventListener("click", () => {
          onSelectRule(rule);
        });
      }
      return body;
    }

    function renderRuleListItem(rule, status) {
      const item = document.createElement("article");
      item.className = "rule-list-item";
      const header = renderRuleListHeader(rule, status);
      header.appendChild(renderRuleListActions(rule, status));
      item.appendChild(header);
      item.appendChild(renderRuleListBody(rule, status));
      return item;
    }

    function render(items) {
      ruleList.innerHTML = "";
      const visibleItems = filterRuleItems(items, currentFilter);
      if (!visibleItems.length) {
        ruleList.appendChild(renderRuleListEmptyState());
        return;
      }
      visibleItems.forEach(({ status, rule }) => {
        ruleList.appendChild(renderRuleListItem(rule, status));
      });
    }

    function setFilter(nextFilter) {
      currentFilter = nextFilter;
      ruleFilterButtons.forEach((button, index) => {
        const fallbackFilter = index === 0 ? "all" : index === 1 ? publishedStatus : unpublishedStatus;
        const buttonFilter = button.dataset.filter || fallbackFilter;
        button.classList.toggle("active", buttonFilter === currentFilter);
      });
    }

    function getFilter() {
      return currentFilter;
    }

    function initFilterButtons(onFilterChange) {
      ruleFilterButtons.forEach((button, index) => {
        const fallbackFilter = index === 0 ? "all" : index === 1 ? publishedStatus : unpublishedStatus;
        if (!button.dataset.filter) {
          button.dataset.filter = fallbackFilter;
        }
        button.addEventListener("click", () => {
          const nextFilter = button.dataset.filter || fallbackFilter;
          setFilter(nextFilter);
          onFilterChange(nextFilter);
        });
      });
    }

    return {
      getFilter,
      initFilterButtons,
      render,
      renderRuleList: render,
      renderRuleListActions,
      renderRuleListBody,
      renderRuleListEmptyState,
      renderRuleListHeader,
      renderRuleListItem,
      setFilter,
    };
  }

  window.AgentGuardRuleListController = {
    create: createRuleListController,
  };
})();
