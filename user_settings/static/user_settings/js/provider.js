/* user_settings/static/user_settings/js/provider.js */
(function () {
  const form = document.getElementById("provider-form");
  if (!form) {
    return;
  }

  const defaultsNode = document.getElementById("provider-defaults-data");
  let providerDefaultsMap = {};
  if (defaultsNode && defaultsNode.textContent) {
    try {
      providerDefaultsMap = JSON.parse(defaultsNode.textContent);
    } catch (_error) {
      providerDefaultsMap = {};
    }
  }

  const providerTypeInput = document.getElementById("id_provider_type");
  const apiKeyInput = document.getElementById("id_api_key");
  const baseUrlInput = document.getElementById("id_base_url");
  const modelInput = document.getElementById("id_model");
  const maxContextInput = document.getElementById("id_max_context_tokens");
  const actionInput = document.getElementById("provider-form-action");

  const saveButton = document.getElementById("save-provider-btn");
  const testButton = document.getElementById("test-provider-btn");
  const refreshButton = document.getElementById("refresh-provider-capabilities-btn");
  const loadModelsButton = document.getElementById("load-provider-models-btn");
  const modelCatalogStatus = document.getElementById("provider-model-catalog-status");
  const modelCatalogControls = document.getElementById("provider-model-catalog-controls");
  const modelCatalogEmpty = document.getElementById("provider-model-catalog-empty");
  const modelCatalogContainer = document.getElementById("provider-model-catalog");
  const modelSearchInput = document.getElementById("provider-model-search");
  const loadedOnlyWrapper = document.getElementById("provider-model-loaded-filter-wrapper");
  const loadedOnlyInput = document.getElementById("provider-model-loaded-only");
  const filterPillsContainer = document.getElementById("provider-model-filter-pills");
  const selectedModelSummary = document.getElementById("provider-selected-model-summary");
  const maxContextNote = document.getElementById("provider-max-context-note");
  const resetMaxContextButton = document.getElementById("provider-reset-max-context-btn");

  const providerId = form.dataset.providerId || "";
  const validationStatusUrl = form.dataset.validationStatusUrl || "";
  const modelCatalogUrl = form.dataset.modelCatalogUrl || "";
  const validationPollIntervalMs = Number.parseInt(
    form.dataset.validationPollIntervalMs || "2000",
    10
  );

  const state = {
    currentValidationStatus: form.dataset.validationStatus || "untested",
    initialConnection: {
      providerType: providerTypeInput ? providerTypeInput.value : "",
      baseUrl: baseUrlInput ? baseUrlInput.value.trim() : "",
      apiKey: apiKeyInput ? apiKeyInput.value : "",
    },
    lastProviderType: providerTypeInput ? providerTypeInput.value : "",
    catalogLoaded: false,
    catalogLoading: false,
    catalogItems: [],
    activeFilters: new Set(),
    selectedCatalogItem: null,
    suggestedMaxContextTokens: null,
    manualMaxContextOverride: false,
    pollingHandle: null,
    hasValidationSnapshot:
      Boolean(testButton) && (testButton.textContent || "").toLowerCase().includes("retest"),
    labels: {
      saveConnection: "Save connection",
      saveProvider: "Save provider",
      loadModels: "Load models",
      refreshModels: "Refresh models",
      testing: "Testing…",
      test: testButton ? testButton.textContent.trim() : "Test selected model",
    },
  };

  const FILTER_DEFINITIONS = [
    {
      key: "image_input",
      label: "Image input",
      matches(item) {
        return getCapabilityStatus(item.input_modalities, "image") === "pass";
      },
    },
    {
      key: "pdf_input",
      label: "PDF input",
      matches(item) {
        return getCapabilityStatus(item.input_modalities, "pdf") === "pass";
      },
    },
    {
      key: "audio_input",
      label: "Audio input",
      matches(item) {
        return getCapabilityStatus(item.input_modalities, "audio") === "pass";
      },
    },
    {
      key: "image_output",
      label: "Image output",
      matches(item) {
        return getCapabilityStatus(item.output_modalities, "image") === "pass";
      },
    },
    {
      key: "audio_output",
      label: "Audio output",
      matches(item) {
        return getCapabilityStatus(item.output_modalities, "audio") === "pass";
      },
    },
    {
      key: "tools",
      label: "Tools",
      matches(item) {
        return getCapabilityStatus(item.operations, "tools") === "pass";
      },
    },
    {
      key: "reasoning",
      label: "Reasoning",
      matches(item) {
        return getCapabilityStatus(item.operations, "reasoning") === "pass";
      },
    },
    {
      key: "structured_output",
      label: "Structured output",
      matches(item) {
        return getCapabilityStatus(item.operations, "structured_output") === "pass";
      },
    },
  ];

  function escapeHtml(value) {
    return String(value || "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function getProviderDefaults(providerType) {
    if (!providerType) {
      return {};
    }
    return providerDefaultsMap[String(providerType)] || {};
  }

  function getCurrentProviderDefaults() {
    return getProviderDefaults(providerTypeInput ? providerTypeInput.value : "");
  }

  function providerSupportsModelCatalog() {
    return Boolean(getCurrentProviderDefaults().supports_model_catalog);
  }

  function providerHasBeenSaved() {
    return providerId !== "";
  }

  function currentModelValue() {
    return modelInput ? modelInput.value.trim() : "";
  }

  function hasSelectedModel() {
    return currentModelValue().length > 0;
  }

  function parseInteger(value) {
    const parsed = Number.parseInt(String(value || "").trim(), 10);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function hasUnsavedConnectionChanges() {
    if (!providerHasBeenSaved()) {
      return false;
    }

    const currentConnection = {
      providerType: providerTypeInput ? providerTypeInput.value : "",
      baseUrl: baseUrlInput ? baseUrlInput.value.trim() : "",
      apiKey: apiKeyInput ? apiKeyInput.value : "",
    };

    return (
      currentConnection.providerType !== state.initialConnection.providerType ||
      currentConnection.baseUrl !== state.initialConnection.baseUrl ||
      currentConnection.apiKey !== state.initialConnection.apiKey
    );
  }

  function getCapabilityStatus(group, key) {
    if (!group || typeof group !== "object") {
      return "unknown";
    }
    const status = group[key];
    if (status === "pass" || status === "unsupported" || status === "unknown") {
      return status;
    }
    return "unknown";
  }

  function statusBadgeClass(status) {
    if (status === "pass") {
      return "text-bg-success";
    }
    if (status === "unsupported") {
      return "text-bg-warning";
    }
    return "text-bg-secondary";
  }

  function isLmStudioProvider() {
    return (providerTypeInput ? providerTypeInput.value : "") === "lmstudio";
  }

  function renderCapabilityBadges(item) {
    const badges = [];
    const groups = [
      { label: "Image input", group: item.input_modalities, key: "image" },
      { label: "PDF input", group: item.input_modalities, key: "pdf" },
      { label: "Audio input", group: item.input_modalities, key: "audio" },
      { label: "Image output", group: item.output_modalities, key: "image" },
      { label: "Audio output", group: item.output_modalities, key: "audio" },
      { label: "Tools", group: item.operations, key: "tools" },
      { label: "Reasoning", group: item.operations, key: "reasoning" },
      { label: "Structured output", group: item.operations, key: "structured_output" },
    ];

    groups.forEach((entry) => {
      if (getCapabilityStatus(entry.group, entry.key) === "pass") {
        badges.push(
          `<span class="badge ${statusBadgeClass("pass")}">${escapeHtml(entry.label)}</span>`
        );
      }
    });

    if (item.state && item.state.loaded === true) {
      badges.push('<span class="badge text-bg-primary">Loaded</span>');
    } else if (item.state && item.state.loaded === false) {
      badges.push('<span class="badge text-bg-secondary">Not loaded</span>');
    }

    return badges.join(" ");
  }

  function renderPricing(item) {
    if (!item.pricing || typeof item.pricing !== "object") {
      return "";
    }

    const entries = Object.entries(item.pricing).filter(([, value]) => value !== null && value !== "");
    if (!entries.length) {
      return "";
    }

    const labelMap = {
      prompt: "Prompt",
      completion: "Completion",
      request: "Request",
      image: "Image",
      input_cache_read: "Cache read",
      input_cache_write: "Cache write",
      web_search: "Web search",
    };

    const parts = entries.map(([key, value]) => {
      return `<span class="me-3"><span class="text-muted">${escapeHtml(
        labelMap[key] || key
      )}:</span> ${escapeHtml(value)}</span>`;
    });

    return `<div class="small mt-2">${parts.join("")}</div>`;
  }

  function renderStateDetails(item) {
    if (!item.provider_metadata || typeof item.provider_metadata !== "object") {
      return "";
    }

    const details = [];
    if (item.provider_metadata.publisher) {
      details.push(`Publisher: ${escapeHtml(item.provider_metadata.publisher)}`);
    }
    if (item.provider_metadata.params_string) {
      details.push(`Params: ${escapeHtml(item.provider_metadata.params_string)}`);
    }
    if (item.provider_metadata.format) {
      details.push(`Format: ${escapeHtml(item.provider_metadata.format)}`);
    }
    if (item.provider_metadata.arch) {
      details.push(`Arch: ${escapeHtml(item.provider_metadata.arch)}`);
    }

    if (!details.length) {
      return "";
    }

    return `<div class="small text-muted mt-2">${details.join(" · ")}</div>`;
  }

  function availableCatalogFilters() {
    return FILTER_DEFINITIONS.filter((definition) =>
      state.catalogItems.some((item) => definition.matches(item))
    );
  }

  function renderFilterPills() {
    if (!filterPillsContainer) {
      return;
    }

    const filters = availableCatalogFilters();
    if (!state.catalogLoaded || !filters.length) {
      filterPillsContainer.innerHTML = "";
      return;
    }

    filterPillsContainer.innerHTML = filters
      .map((definition) => {
        const isActive = state.activeFilters.has(definition.key);
        const buttonClass = isActive ? "btn-primary" : "btn-outline-secondary";
        return `
          <button
            type="button"
            class="btn btn-sm ${buttonClass}"
            data-provider-model-filter="${escapeHtml(definition.key)}"
          >
            ${escapeHtml(definition.label)}
          </button>
        `;
      })
      .join("");

    filterPillsContainer
      .querySelectorAll("[data-provider-model-filter]")
      .forEach((button) => {
        button.addEventListener("click", () => {
          const filterKey = button.dataset.providerModelFilter;
          if (!filterKey) {
            return;
          }
          if (state.activeFilters.has(filterKey)) {
            state.activeFilters.delete(filterKey);
          } else {
            state.activeFilters.add(filterKey);
          }
          renderFilterPills();
          renderCatalog();
        });
      });
  }

  function catalogSearchHaystack(item) {
    const providerMetadata = item.provider_metadata || {};
    return [
      item.id,
      item.label,
      item.description,
      providerMetadata.publisher,
      providerMetadata.model_key,
      providerMetadata.arch,
      providerMetadata.format,
      providerMetadata.params_string,
      Object.keys(item.input_modalities || {}).join(" "),
      Object.keys(item.output_modalities || {}).join(" "),
      Object.keys(item.operations || {}).join(" "),
    ]
      .join(" ")
      .toLowerCase();
  }

  function getFilteredCatalogItems() {
    let items = [...state.catalogItems];

    const searchTerm = modelSearchInput ? modelSearchInput.value.trim().toLowerCase() : "";
    if (searchTerm) {
      items = items.filter((item) => catalogSearchHaystack(item).includes(searchTerm));
    }

    if (loadedOnlyInput && loadedOnlyInput.checked && isLmStudioProvider()) {
      items = items.filter((item) => item.state && item.state.loaded === true);
    }

    if (state.activeFilters.size) {
      items = items.filter((item) =>
        [...state.activeFilters].every((filterKey) => {
          const definition = FILTER_DEFINITIONS.find((entry) => entry.key === filterKey);
          return definition ? definition.matches(item) : true;
        })
      );
    }

    return items;
  }

  function updateSelectedModelSummary() {
    if (!selectedModelSummary) {
      return;
    }

    const currentModel = currentModelValue();
    if (state.selectedCatalogItem) {
      const item = state.selectedCatalogItem;
      const contextText = item.context_length
        ? `<div class="small text-muted mt-1">Context length: ${escapeHtml(item.context_length)}</div>`
        : "";
      const descriptionText = item.description
        ? `<div class="small text-muted mt-2">${escapeHtml(item.description)}</div>`
        : "";

      selectedModelSummary.className = "border rounded p-3 bg-light-subtle small";
      selectedModelSummary.innerHTML = `
        <div class="d-flex justify-content-between align-items-start gap-3">
          <div>
            <div class="fw-semibold">${escapeHtml(item.label || item.id)}</div>
            <div class="text-muted mt-1"><code>${escapeHtml(item.id)}</code></div>
            ${descriptionText}
            ${contextText}
          </div>
        </div>
        <div class="d-flex flex-wrap gap-2 mt-3">${renderCapabilityBadges(item)}</div>
        ${renderPricing(item)}
        ${renderStateDetails(item)}
      `;
      return;
    }

    if (currentModel && state.catalogLoaded) {
      selectedModelSummary.className = "border rounded p-3 bg-warning-subtle small";
      selectedModelSummary.innerHTML = `
        <div class="fw-semibold">${escapeHtml(currentModel)}</div>
        <div class="mt-1">
          The current model is not present in the live catalog. It will be kept until you replace it.
        </div>
      `;
      return;
    }

    if (currentModel) {
      selectedModelSummary.className = "border rounded p-3 bg-light-subtle small";
      selectedModelSummary.innerHTML = `
        <div class="fw-semibold">${escapeHtml(currentModel)}</div>
        <div class="mt-1 text-muted">This model is set manually.</div>
      `;
      return;
    }

    selectedModelSummary.className = "border rounded p-3 bg-light-subtle small text-muted";
    selectedModelSummary.textContent =
      "No model selected yet. You can save this provider as a connection only, then choose a model later.";
  }

  function syncManualMaxContextState() {
    if (!maxContextInput) {
      return;
    }

    const currentValue = parseInteger(maxContextInput.value);
    if (state.suggestedMaxContextTokens == null) {
      state.manualMaxContextOverride = currentValue !== null;
      return;
    }

    state.manualMaxContextOverride = currentValue !== state.suggestedMaxContextTokens;
  }

  function updateMaxContextUi() {
    if (!maxContextNote) {
      return;
    }

    const suggestedValue = state.suggestedMaxContextTokens;
    if (suggestedValue == null) {
      maxContextNote.textContent = providerSupportsModelCatalog()
        ? "Select a model to get a suggested context window, or enter a manual override."
        : "Set this manually for providers without a model catalog, or after selecting a model.";
      if (resetMaxContextButton) {
        resetMaxContextButton.classList.add("d-none");
      }
      return;
    }

    if (state.manualMaxContextOverride) {
      maxContextNote.textContent = `Manual override. Suggested by model metadata: ${suggestedValue}.`;
      if (resetMaxContextButton) {
        resetMaxContextButton.classList.remove("d-none");
      }
      return;
    }

    maxContextNote.textContent = `Suggested by model metadata: ${suggestedValue}.`;
    if (resetMaxContextButton) {
      resetMaxContextButton.classList.add("d-none");
    }
  }

  function applySuggestedMaxContextTokens(options) {
    if (!maxContextInput) {
      return;
    }

    const preserveManual = options && options.preserveManual === true;
    const suggestedValue = state.suggestedMaxContextTokens;
    if (suggestedValue == null) {
      syncManualMaxContextState();
      updateMaxContextUi();
      return;
    }

    if (!preserveManual || !state.manualMaxContextOverride || !parseInteger(maxContextInput.value)) {
      maxContextInput.value = String(suggestedValue);
      state.manualMaxContextOverride = false;
    }

    syncManualMaxContextState();
    updateMaxContextUi();
  }

  function syncSelectedCatalogItemFromModel() {
    const currentModel = currentModelValue();
    state.selectedCatalogItem = state.catalogItems.find((item) => item.id === currentModel) || null;
    state.suggestedMaxContextTokens = state.selectedCatalogItem
      ? parseInteger(
          state.selectedCatalogItem.suggested_max_context_tokens ||
            state.selectedCatalogItem.context_length
        )
      : null;
    syncManualMaxContextState();
    updateSelectedModelSummary();
    updateMaxContextUi();
  }

  function selectCatalogItem(modelId) {
    if (!modelInput) {
      return;
    }

    const nextItem = state.catalogItems.find((item) => item.id === modelId) || null;
    if (!nextItem) {
      return;
    }

    modelInput.value = nextItem.id;
    state.selectedCatalogItem = nextItem;
    state.suggestedMaxContextTokens = parseInteger(
      nextItem.suggested_max_context_tokens || nextItem.context_length
    );
    applySuggestedMaxContextTokens({ preserveManual: true });
    updateSelectedModelSummary();
    renderCatalog();
    updateActionButtons();
  }

  function renderCatalog() {
    if (!modelCatalogContainer || !modelCatalogEmpty || !modelCatalogControls) {
      return;
    }

    if (!providerSupportsModelCatalog()) {
      modelCatalogControls.classList.add("d-none");
      modelCatalogContainer.innerHTML = "";
      modelCatalogEmpty.classList.remove("alert-danger");
      modelCatalogEmpty.classList.add("alert-light");
      modelCatalogEmpty.classList.remove("d-none");
      modelCatalogEmpty.textContent =
        "This provider uses manual model entry. Enter the model id directly below.";
      updateSelectedModelSummary();
      updateMaxContextUi();
      return;
    }

    if (!providerHasBeenSaved()) {
      modelCatalogControls.classList.add("d-none");
      modelCatalogContainer.innerHTML = "";
      modelCatalogEmpty.classList.remove("alert-danger");
      modelCatalogEmpty.classList.add("alert-light");
      modelCatalogEmpty.classList.remove("d-none");
      modelCatalogEmpty.textContent =
        "Save this connection first, then load the provider model catalog if available.";
      updateSelectedModelSummary();
      updateMaxContextUi();
      return;
    }

    if (hasUnsavedConnectionChanges()) {
      modelCatalogControls.classList.add("d-none");
      modelCatalogContainer.innerHTML = "";
      modelCatalogEmpty.classList.remove("alert-danger");
      modelCatalogEmpty.classList.add("alert-light");
      modelCatalogEmpty.classList.remove("d-none");
      modelCatalogEmpty.textContent =
        "Save connection changes before loading the model catalog for this provider.";
      updateSelectedModelSummary();
      updateMaxContextUi();
      return;
    }

    if (!state.catalogLoaded) {
      modelCatalogControls.classList.add("d-none");
      modelCatalogContainer.innerHTML = "";
      modelCatalogEmpty.classList.remove("alert-danger");
      modelCatalogEmpty.classList.add("alert-light");
      modelCatalogEmpty.classList.remove("d-none");
      modelCatalogEmpty.textContent =
        "Load the provider model catalog to search available models and populate the model field.";
      updateSelectedModelSummary();
      updateMaxContextUi();
      return;
    }

    const items = getFilteredCatalogItems();
    modelCatalogControls.classList.remove("d-none");
    modelCatalogEmpty.classList.toggle("d-none", items.length > 0);
    if (!items.length) {
      modelCatalogEmpty.classList.remove("alert-danger");
      modelCatalogEmpty.classList.add("alert-light");
      modelCatalogEmpty.textContent = "No models matched the current filters.";
      modelCatalogContainer.innerHTML = "";
      updateSelectedModelSummary();
      updateMaxContextUi();
      return;
    }

    modelCatalogContainer.innerHTML = items
      .map((item) => {
        const isSelected = state.selectedCatalogItem && state.selectedCatalogItem.id === item.id;
        const selectedClass = isSelected ? "border-primary shadow-sm" : "border-light-subtle";
        const buttonClass = isSelected ? "btn-primary" : "btn-outline-primary";
        const contextText = item.context_length
          ? `<span class="text-muted">Context:</span> ${escapeHtml(item.context_length)}`
          : "";

        return `
          <div class="col-lg-6">
            <div class="card h-100 ${selectedClass}">
              <div class="card-body">
                <div class="d-flex justify-content-between align-items-start gap-3">
                  <div>
                    <div class="fw-semibold">${escapeHtml(item.label || item.id)}</div>
                    <div class="small text-muted mt-1"><code>${escapeHtml(item.id)}</code></div>
                  </div>
                  <button
                    type="button"
                    class="btn btn-sm ${buttonClass}"
                    data-provider-model-select="${escapeHtml(item.id)}"
                  >
                    ${isSelected ? "Selected" : "Select"}
                  </button>
                </div>
                ${
                  item.description
                    ? `<div class="small text-muted mt-3">${escapeHtml(item.description)}</div>`
                    : ""
                }
                ${
                  contextText
                    ? `<div class="small mt-3">${contextText}</div>`
                    : ""
                }
                <div class="d-flex flex-wrap gap-2 mt-3">${renderCapabilityBadges(item)}</div>
                ${renderPricing(item)}
                ${renderStateDetails(item)}
              </div>
            </div>
          </div>
        `;
      })
      .join("");

    modelCatalogContainer
      .querySelectorAll("[data-provider-model-select]")
      .forEach((button) => {
        button.addEventListener("click", () => {
          const modelId = button.dataset.providerModelSelect;
          if (modelId) {
            selectCatalogItem(modelId);
          }
        });
      });

    updateSelectedModelSummary();
    updateMaxContextUi();
  }

  function setCatalogStatus(message, tone) {
    if (!modelCatalogStatus) {
      return;
    }

    modelCatalogStatus.className = "small mt-3";
    if (tone === "danger") {
      modelCatalogStatus.classList.add("text-danger");
    } else if (tone === "success") {
      modelCatalogStatus.classList.add("text-success");
    } else {
      modelCatalogStatus.classList.add("text-muted");
    }
    modelCatalogStatus.textContent = message || "";
  }

  async function loadModelCatalog() {
    if (!loadModelsButton || state.catalogLoading || !providerSupportsModelCatalog()) {
      return;
    }
    if (!providerHasBeenSaved()) {
      renderCatalog();
      return;
    }
    if (hasUnsavedConnectionChanges()) {
      renderCatalog();
      return;
    }
    if (!modelCatalogUrl) {
      setCatalogStatus("This provider does not expose a catalog endpoint.", "danger");
      return;
    }

    state.catalogLoading = true;
    updateActionButtons();
    setCatalogStatus("Loading provider model catalog…", "muted");

    try {
      const response = await fetch(modelCatalogUrl, {
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || `HTTP ${response.status}`);
      }

      state.catalogItems = Array.isArray(payload.models) ? payload.models : [];
      state.catalogLoaded = true;
      syncSelectedCatalogItemFromModel();
      renderFilterPills();
      renderCatalog();
      setCatalogStatus(`${state.catalogItems.length} model(s) loaded.`, "success");
    } catch (error) {
      state.catalogLoaded = false;
      state.catalogItems = [];
      renderFilterPills();
      renderCatalog();
      setCatalogStatus(
        error instanceof Error ? error.message : "Could not load provider model catalog.",
        "danger"
      );
    } finally {
      state.catalogLoading = false;
      updateActionButtons();
    }
  }

  function updateSaveButtonLabel() {
    if (!saveButton) {
      return;
    }
    const label = hasSelectedModel() ? state.labels.saveProvider : state.labels.saveConnection;
    saveButton.innerHTML = `<i class="bi bi-check-lg me-1"></i>${escapeHtml(label)}`;
  }

  function updateTestButtonLabel() {
    if (!testButton) {
      return;
    }
    const label =
      state.currentValidationStatus === "testing" ? state.labels.testing : state.labels.test;
    testButton.innerHTML = `<i class="bi bi-plug me-1"></i>${escapeHtml(label)}`;
  }

  function updateLoadButtonLabel() {
    if (!loadModelsButton) {
      return;
    }
    const label = state.catalogLoaded ? state.labels.refreshModels : state.labels.loadModels;
    loadModelsButton.innerHTML = `<i class="bi bi-arrow-repeat me-1"></i>${escapeHtml(label)}`;
  }

  function updateActionButtons() {
    updateSaveButtonLabel();
    updateTestButtonLabel();
    updateLoadButtonLabel();

    const validationLocked = state.currentValidationStatus === "testing";

    if (testButton) {
      testButton.disabled = validationLocked || !hasSelectedModel();
    }

    if (refreshButton) {
      refreshButton.disabled = validationLocked || !hasSelectedModel();
    }

    if (loadModelsButton) {
      loadModelsButton.classList.toggle("d-none", !providerSupportsModelCatalog());
      loadModelsButton.disabled =
        state.catalogLoading ||
        !providerSupportsModelCatalog() ||
        !providerHasBeenSaved() ||
        hasUnsavedConnectionChanges();
    }

    if (loadedOnlyWrapper) {
      loadedOnlyWrapper.classList.toggle("d-none", !isLmStudioProvider());
    }
  }

  function resetCatalogState(options) {
    const keepStatus = options && options.keepStatus === true;
    state.catalogLoaded = false;
    state.catalogItems = [];
    state.activeFilters.clear();
    state.selectedCatalogItem = null;
    state.suggestedMaxContextTokens = null;
    if (modelSearchInput) {
      modelSearchInput.value = "";
    }
    if (loadedOnlyInput) {
      loadedOnlyInput.checked = false;
    }
    renderFilterPills();
    renderCatalog();
    if (!keepStatus) {
      setCatalogStatus("", "muted");
    }
  }

  function syncProviderDefaults(previousProviderType) {
    if (!providerTypeInput) {
      return;
    }

    const previousDefaults = getProviderDefaults(previousProviderType);
    const nextDefaults = getCurrentProviderDefaults();

    if (baseUrlInput) {
      const currentBaseUrl = baseUrlInput.value.trim();
      const previousDefaultBaseUrl = previousDefaults.default_base_url || "";
      if (currentBaseUrl === "" || currentBaseUrl === previousDefaultBaseUrl) {
        baseUrlInput.value = nextDefaults.default_base_url || "";
      }
    }

    if (maxContextInput && !hasSelectedModel()) {
      const currentMaxContext = maxContextInput.value.trim();
      const previousDefaultMaxContext =
        previousDefaults.default_max_context_tokens != null
          ? String(previousDefaults.default_max_context_tokens)
          : "";
      if (currentMaxContext === "" || currentMaxContext === previousDefaultMaxContext) {
        if (nextDefaults.default_max_context_tokens != null) {
          maxContextInput.value = String(nextDefaults.default_max_context_tokens);
        }
      }
    }

    if (apiKeyInput) {
      if (nextDefaults.api_key_required === false) {
        apiKeyInput.required = false;
      }
    }
  }

  async function pollValidationStatus() {
    if (!validationStatusUrl || state.currentValidationStatus !== "testing") {
      return;
    }

    try {
      const response = await fetch(validationStatusUrl, {
        headers: {
          Accept: "application/json",
          "X-Requested-With": "XMLHttpRequest",
        },
        credentials: "same-origin",
      });
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      if (!payload || !payload.validation_status) {
        return;
      }

      if (payload.validation_status !== "testing") {
        window.location.reload();
      }
    } catch (_error) {
      // Keep polling silently while validation is in progress.
    }
  }

  function startValidationPolling() {
    if (!validationStatusUrl || state.currentValidationStatus !== "testing" || state.pollingHandle) {
      return;
    }

    state.pollingHandle = window.setInterval(
      pollValidationStatus,
      Number.isFinite(validationPollIntervalMs) ? validationPollIntervalMs : 2000
    );
    pollValidationStatus();
  }

  function bindEvents() {
    if (providerTypeInput) {
      providerTypeInput.addEventListener("change", () => {
        const previousProviderType = state.lastProviderType;
        syncProviderDefaults(previousProviderType);
        state.lastProviderType = providerTypeInput.value;
        resetCatalogState({ keepStatus: false });
        updateActionButtons();
      });
    }

    if (baseUrlInput || apiKeyInput) {
      [baseUrlInput, apiKeyInput].forEach((input) => {
        if (!input) {
          return;
        }
        input.addEventListener("input", () => {
          updateActionButtons();
          renderCatalog();
        });
      });
    }

    if (modelInput) {
      modelInput.addEventListener("input", () => {
        syncSelectedCatalogItemFromModel();
        renderCatalog();
        updateActionButtons();
      });
    }

    if (maxContextInput) {
      maxContextInput.addEventListener("input", () => {
        syncManualMaxContextState();
        updateMaxContextUi();
      });
    }

    if (resetMaxContextButton) {
      resetMaxContextButton.addEventListener("click", () => {
        if (state.suggestedMaxContextTokens == null || !maxContextInput) {
          return;
        }
        maxContextInput.value = String(state.suggestedMaxContextTokens);
        state.manualMaxContextOverride = false;
        updateMaxContextUi();
      });
    }

    if (loadModelsButton) {
      loadModelsButton.addEventListener("click", () => {
        loadModelCatalog();
      });
    }

    if (modelSearchInput) {
      modelSearchInput.addEventListener("input", renderCatalog);
    }

    if (loadedOnlyInput) {
      loadedOnlyInput.addEventListener("change", renderCatalog);
    }

    form.querySelectorAll("[data-form-action]").forEach((button) => {
      button.addEventListener("click", () => {
        if (actionInput) {
          actionInput.value = button.dataset.formAction || "";
        }
      });
    });

    form.addEventListener("submit", (event) => {
      const submitter = event.submitter;
      if (actionInput) {
        actionInput.value = submitter ? submitter.dataset.formAction || "" : "";
      }

      if (actionInput && actionInput.value === "test_provider") {
        if (!hasSelectedModel()) {
          event.preventDefault();
          updateActionButtons();
          return;
        }
        state.currentValidationStatus = "testing";
        updateActionButtons();
      }
    });
  }

  syncSelectedCatalogItemFromModel();
  updateActionButtons();
  renderFilterPills();
  renderCatalog();
  bindEvents();
  startValidationPolling();
})();
