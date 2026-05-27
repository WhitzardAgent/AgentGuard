(function () {
  const PATH_WILDCARDS = ["*", "...", "...?"];
  const PATH_WILDCARD_LABELS = {
    "*": "* - exactly one tool between",
    "...": "... - one or more tools between",
    "...?": "...? - zero or more tools between",
  };
  const uiHelpers = window.AgentGuardUIHelpers || {};
  const createIconButton = uiHelpers.createIconButton || function fallbackCreateIconButton(iconName, ariaLabel, onClick) {
    const button = document.createElement("button");
    button.className = "condition-icon-button";
    button.type = "button";
    button.setAttribute("aria-label", ariaLabel);

    const icon = document.createElement("img");
    icon.className = "condition-action-icon";
    icon.src = `/assets/${iconName}`;
    icon.alt = "";
    button.appendChild(icon);

    button.addEventListener("click", onClick);
    return button;
  };

  function isWildcard(value) {
    return PATH_WILDCARDS.includes(value);
  }

  function nextLabel(label) {
    const code = String(label || "A").toUpperCase().charCodeAt(0);
    if (Number.isNaN(code) || code < 65 || code >= 90) {
      return "Z";
    }
    return String.fromCharCode(code + 1);
  }

  function createSegment(label, value) {
    return {
      label: String(label || "A").trim() || "A",
      value: String(value || label || "A").trim() || "A",
    };
  }

  function segmentsFromPath(path) {
    const values = String(path || "")
      .split("->")
      .map((segment) => segment.trim())
      .filter(Boolean);

    if (!values.length) {
      return [];
    }

    let currentLabel = "A";
    return values.map((value) => {
      const label = isWildcard(value) ? currentLabel : value;
      const segment = createSegment(label, value);
      currentLabel = nextLabel(segment.label);
      return segment;
    });
  }

  function normalizeSegments(segments) {
    if (!Array.isArray(segments) || !segments.length) {
      return [];
    }

    const normalized = segments
      .map((segment) => {
        if (typeof segment === "string") {
          return createSegment(segment, segment);
        }
        return createSegment(segment?.label, segment?.value);
      })
      .filter((segment) => segment.value);

    return normalized;
  }

  function normalizeValue(value) {
    const segments = Array.isArray(value?.pathSlots) && value.pathSlots.length
      ? normalizeSegments(value.pathSlots)
      : segmentsFromPath(value?.path || "");

    return {
      path: segments.map((segment) => segment.value).join("->"),
      pathSlots: segments.map((segment) => ({ label: segment.label, value: segment.value })),
      finished: Boolean(value?.finished),
    };
  }

  function validatePathState(pathState) {
    const currentSegments = normalizeSegments(pathState?.pathSlots || segmentsFromPath(pathState?.path || ""));
    if (!currentSegments.length) {
      return { ok: false, message: "PATH must contain at least one concrete segment." };
    }
    if (isWildcard(currentSegments[0].value)) {
      return { ok: false, message: "PATH must start with A." };
    }
    if (isWildcard(currentSegments[currentSegments.length - 1].value)) {
      return { ok: false, message: "PATH cannot end with a wildcard segment." };
    }
    return { ok: true, message: "PATH is valid." };
  }

  function createPathBuilder(options) {
    const root = options.root;
    const hint = options.hint;
    const onChange = options.onChange || (() => {});
    let segments = [];
    let finished = false;

    function validate(currentSegments = segments) {
      return validatePathState({ pathSlots: currentSegments });
    }

    function syncHint() {
      if (!segments.length) {
        hint.textContent = "Build PATH by adding one or more concrete or wildcard segments.";
        hint.classList.remove("path-builder-error");
        return;
      }
      const result = validate();
      hint.textContent = result.message;
      hint.classList.toggle("path-builder-error", !result.ok);
    }

    function sync(emitChange = true) {
      render();
      syncHint();
      if (emitChange) {
        onChange(api.getValue());
      }
    }

    function optionsFor(segment, index) {
      return index === 0 ? [segment.label] : [segment.label, ...PATH_WILDCARDS];
    }

    function optionLabel(value) {
      return PATH_WILDCARD_LABELS[value] || value;
    }

    function removeSegment(index) {
      segments.splice(index, 1);
      finished = false;
      sync();
    }

    function modifyPath() {
      finished = false;
      sync();
    }

    function renderSummary() {
      const summary = document.createElement("div");
      summary.className = "path-summary-line";

      const label = document.createElement("span");
      label.className = "path-summary-label";
      label.textContent = "PATH:";
      summary.appendChild(label);

      const text = document.createElement("div");
      text.className = "path-summary-value";
      text.textContent = segments.map((segment) => segment.value).join(" -> ");
      summary.appendChild(text);

      root.appendChild(summary);
    }

    function renderEditor() {
      if (!segments.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        empty.textContent = "PATH is empty. Click + to add the first segment.";
        root.appendChild(empty);
        return;
      }

      segments.forEach((segment, index) => {
        const row = document.createElement("div");
        row.className = "path-segment-row";

        const step = document.createElement("span");
        step.className = "path-segment-step";
        step.textContent = index === 0 ? "Start" : `Step ${index + 1}`;

        const field = document.createElement("div");
        field.className = "field path-segment-field";

        const select = document.createElement("select");
        optionsFor(segment, index).forEach((value) => {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = optionLabel(value);
          option.selected = value === segment.value;
          select.appendChild(option);
        });
        select.addEventListener("change", (event) => {
          segments[index].value = event.target.value;
          finished = false;
          sync();
        });

        field.appendChild(select);
        row.appendChild(step);
        row.appendChild(field);

        const actions = document.createElement("div");
        actions.className = "path-row-actions";
        if (index > 0) {
          actions.appendChild(createIconButton("close.png", "Delete path segment", () => removeSegment(index)));
        } else {
          actions.setAttribute("aria-hidden", "true");
          actions.classList.add("path-row-actions-placeholder");
        }
        row.appendChild(actions);

        root.appendChild(row);

        if (index < segments.length - 1) {
          const arrow = document.createElement("div");
          arrow.className = "path-arrow";
          arrow.textContent = "->";
          root.appendChild(arrow);
        }
      });
    }

    function render() {
      root.innerHTML = "";
      if (finished) {
        renderSummary();
        return;
      }
      renderEditor();
    }

    const api = {
      getValue() {
        return {
          path: segments.map((segment) => segment.value).join("->"),
          pathSlots: segments.map((segment) => ({ label: segment.label, value: segment.value })),
          finished,
        };
      },
      setValue(value, nextFinished = false) {
        const hasExplicitEmpty = Array.isArray(value?.pathSlots) && value.pathSlots.length === 0;
        const nextSegments = hasExplicitEmpty
          ? []
          : Array.isArray(value?.pathSlots) && value.pathSlots.length
            ? value.pathSlots
            : segmentsFromPath(value?.path || "");
        segments = normalizeSegments(nextSegments);
        finished = nextFinished;
        sync();
      },
      appendSegment() {
        const label = segments.length
          ? nextLabel(segments[segments.length - 1]?.label || "A")
          : "A";
        segments.push(createSegment(label, label));
        finished = false;
        sync();
      },
      clear() {
        segments = [];
        finished = false;
        sync();
      },
      modify() {
        modifyPath();
      },
      finish() {
        const result = validate();
        if (result.ok) {
          finished = true;
        }
        sync();
        return result;
      },
      validate() {
        return validate();
      },
      render,
    };

    sync(false);
    return api;
  }

  window.AgentGuardPathBuilder = {
    createPathBuilder,
    normalizeValue,
    validatePathState,
  };
})();
