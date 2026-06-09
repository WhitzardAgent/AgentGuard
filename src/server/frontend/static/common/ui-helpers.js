(function () {
  function actionTone(action) {
    const normalized = String(action || "").trim().toUpperCase();
    if (normalized === "DENY") {
      return "danger";
    }
    if (normalized === "HUMAN_CHECK" || normalized === "LLM_CHECK" || normalized === "DEGRADE") {
      return "warn";
    }
    return "";
  }

  function createIconButton(iconName, ariaLabel, onClick, options = {}) {
    const button = document.createElement("button");
    button.className = String(options.className || "condition-icon-button");
    button.type = "button";
    button.setAttribute("aria-label", ariaLabel);
    if (options.title) {
      button.setAttribute("title", options.title);
    }

    const icon = document.createElement("img");
    icon.className = String(options.iconClassName || "condition-action-icon");
    icon.src = options.iconPathPrefix ? `${options.iconPathPrefix}${iconName}` : `/assets/${iconName}`;
    icon.alt = "";
    button.appendChild(icon);

    button.addEventListener("click", onClick);
    return button;
  }

  window.AgentGuardUIHelpers = {
    actionTone,
    createIconButton,
  };
})();
