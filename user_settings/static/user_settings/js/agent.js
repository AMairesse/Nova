/* user_settings/static/user_settings/js/agent.js */
(function () {
  // ---------------------------------------------------------------------------
  // Toggle tool description when "is_tool" is checked
  // ---------------------------------------------------------------------------
  const isTool = document.querySelector("#id_is_tool");
  const toolDescriptionWrapper = document.getElementById("tool-description-wrapper");
  const providerSelect = document.getElementById("id_llm_provider");
  const providerWarning = document.getElementById("agent-provider-tool-warning");
  const providerCapabilitiesNode = document.getElementById("agent-provider-capabilities");
  let providerCapabilities = {};

  if (providerCapabilitiesNode?.textContent) {
    try {
      providerCapabilities = JSON.parse(providerCapabilitiesNode.textContent);
    } catch (error) {
      console.warn("Failed to parse agent provider capabilities:", error);
    }
  }

  function syncToolDescriptionVisibility() {
    if (!isTool || !toolDescriptionWrapper) return;
    toolDescriptionWrapper.classList.toggle("d-none", !isTool.checked);
  }

  function hasSelectedToolDependencies() {
    const toolsSelect = document.getElementById("id_tools");
    const selectedTools = toolsSelect
      ? Array.from(toolsSelect.options || []).some((option) => option.selected && `${option.value || ""}`.trim())
      : false;
    const selectedAgentTools = Array.from(
      document.querySelectorAll('input[name="agent_tools"]:checked')
    ).some((input) => `${input.value || ""}`.trim());
    return selectedTools || selectedAgentTools;
  }

  function syncProviderToolWarning() {
    if (!providerWarning || !providerSelect) return;

    const providerInfo = providerCapabilities?.[`${providerSelect.value || ""}`] || {};
    const toolsStatus = `${providerInfo.tools_status || ""}`.trim();
    const providerIsToolLess = toolsStatus === "unsupported" || toolsStatus === "fail";

    if (!providerIsToolLess) {
      providerWarning.textContent = "";
      providerWarning.classList.add("d-none");
      return;
    }

    let message = "";
    if (hasSelectedToolDependencies()) {
      message = gettext(
        "This provider/model was verified without tool support, but this agent currently depends on tools or sub-agents."
      );
    } else if (!isTool?.checked) {
      message = gettext(
        "This provider/model was verified without tool support. Simple thread runs can still work in tool-less mode, but this agent will not be usable in continuous mode."
      );
    }

    if (!message) {
      providerWarning.textContent = "";
      providerWarning.classList.add("d-none");
      return;
    }

    providerWarning.textContent = message;
    providerWarning.classList.remove("d-none");
  }

  if (isTool && toolDescriptionWrapper) {
    isTool.addEventListener("change", syncToolDescriptionVisibility);
    syncToolDescriptionVisibility();
  }

  // ---------------------------------------------------------------------------
  // Dual-list UI for Agent tools (ManyToMany "tools" field)
  //
  // Behavior:
  // - Keep Django's original multi-select #id_tools as the source of truth.
  // - Render two lists:
  //     - Available tools
  //     - Selected tools
  // - Clicking moves items between lists and updates the hidden select.
  // - No backend changes required.
  // ---------------------------------------------------------------------------

  const toolsSelect = document.getElementById("id_tools");
  if (toolsSelect) {
    // Avoid double-initialization if HTMX or partial reloads re-run this script
    if (toolsSelect.dataset.dualListInitialized !== "1") {
      toolsSelect.dataset.dualListInitialized = "1";

      // Hide the original select visually but keep it in the form
      toolsSelect.classList.add("d-none");

      // Create containers
      const wrapper = document.createElement("div");
      wrapper.className = "row g-3 mt-2";

      const availableCol = document.createElement("div");
      availableCol.className = "col-6";

      const selectedCol = document.createElement("div");
      selectedCol.className = "col-6";

      // Titles
      const availableTitle = document.createElement("label");
      availableTitle.className = "form-label fw-semibold";
      availableTitle.textContent = "Available tools";

      const selectedTitle = document.createElement("label");
      selectedTitle.className = "form-label fw-semibold";
      selectedTitle.textContent = "Selected tools";

      // Lists
      const availableList = document.createElement("div");
      availableList.className = "list-group small border rounded overflow-auto";
      availableList.style.maxHeight = "220px";

      const selectedList = document.createElement("div");
      selectedList.className = "list-group small border rounded overflow-auto";
      selectedList.style.maxHeight = "220px";

      availableCol.appendChild(availableTitle);
      availableCol.appendChild(availableList);

      selectedCol.appendChild(selectedTitle);
      selectedCol.appendChild(selectedList);

      wrapper.appendChild(availableCol);
      wrapper.appendChild(selectedCol);

      // Insert the dual-list UI right after the hidden select
      toolsSelect.parentNode.insertBefore(wrapper, toolsSelect.nextSibling);

      function createItem(option) {
        const item = document.createElement("button");
        item.type = "button";
        item.className =
          "list-group-item list-group-item-action d-flex justify-content-between align-items-center py-1 px-2";
        item.dataset.value = option.value;

        const label = document.createElement("span");
        label.textContent = option.textContent || option.value;
        label.className = "text-truncate";

        const icon = document.createElement("span");
        icon.className = "ms-2 text-muted";
        icon.innerHTML = "&#x2795;";

        item.appendChild(label);
        item.appendChild(icon);

        return item;
      }

      function createSelectedItem(option) {
        const item = document.createElement("button");
        item.type = "button";
        item.className =
          "list-group-item list-group-item-action d-flex justify-content-between align-items-center py-1 px-2";
        item.dataset.value = option.value;

        const label = document.createElement("span");
        label.textContent = option.textContent || option.value;
        label.className = "text-truncate";

        const remove = document.createElement("span");
        remove.className = "badge bg-secondary border-0 ms-2";
        remove.innerHTML = "&times;";

        item.appendChild(label);
        item.appendChild(remove);

        return item;
      }

      function rebuildLists() {
        availableList.innerHTML = "";
        selectedList.innerHTML = "";

        Array.from(toolsSelect.options).forEach((opt) => {
          if (!opt.value) return;
          if (opt.selected) {
            const item = createSelectedItem(opt);
            item.addEventListener("click", () => unselectTool(opt.value));
            selectedList.appendChild(item);
          } else {
            const item = createItem(opt);
            item.addEventListener("click", () => selectTool(opt.value));
            availableList.appendChild(item);
          }
        });

        if (!availableList.children.length) {
          const empty = document.createElement("div");
          empty.className = "list-group-item text-muted small py-1";
          empty.textContent = "No more tools";
          availableList.appendChild(empty);
        }
        if (!selectedList.children.length) {
          const empty = document.createElement("div");
          empty.className = "list-group-item text-muted small py-1";
          empty.textContent = "No tools selected";
          selectedList.appendChild(empty);
        }

        syncProviderToolWarning();
      }

      function selectTool(value) {
        const opt = Array.from(toolsSelect.options).find(
          (o) => o.value === value
        );
        if (!opt) return;
        opt.selected = true;
        rebuildLists();
      }

      function unselectTool(value) {
        const opt = Array.from(toolsSelect.options).find(
          (o) => o.value === value
        );
        if (!opt) return;
        opt.selected = false;
        rebuildLists();
      }

      rebuildLists();
    }
  }

  if (providerSelect) {
    providerSelect.addEventListener("change", syncProviderToolWarning);
  }
  if (isTool) {
    isTool.addEventListener("change", syncProviderToolWarning);
  }
  document.querySelectorAll('input[name="agent_tools"]').forEach((input) => {
    input.addEventListener("change", syncProviderToolWarning);
  });
  syncProviderToolWarning();
})();
