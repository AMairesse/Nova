/* user_settings/static/user_settings/js/agent.js */
(function () {
  // ---------------------------------------------------------------------------
  // Toggle tool description when "is_tool" is checked
  // ---------------------------------------------------------------------------
  const isTool = document.querySelector("#id_is_tool");
  const toolDescriptionWrapper = document.getElementById("tool-description-wrapper");

  function syncToolDescriptionVisibility() {
    if (!isTool || !toolDescriptionWrapper) return;
    toolDescriptionWrapper.classList.toggle("d-none", !isTool.checked);
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
  if (!toolsSelect) {
    return;
  }

  // Avoid double-initialization if HTMX or partial reloads re-run this script
  if (toolsSelect.dataset.dualListInitialized === "1") {
    return;
  }
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

  // Helper to create an item element
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
    icon.innerHTML = "&#x2795;"; // plus sign

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

  // Sync from select to lists
  function rebuildLists() {
    availableList.innerHTML = "";
    selectedList.innerHTML = "";

    Array.from(toolsSelect.options).forEach((opt) => {
      if (!opt.value) return; // skip empty placeholder if any
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

    // Empty states
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

  // Initial build
  rebuildLists();
})();
