/* user_settings/static/user_settings/js/provider.js */
(function () {
  window.NovaApp = window.NovaApp || {};
  window.NovaApp.Modules = window.NovaApp.Modules || {};

  const form = document.getElementById("provider-form");
  if (!form) {
    return;
  }

  const createProviderCatalogController =
    window.NovaApp.Modules.createProviderCatalogController;
  if (typeof createProviderCatalogController !== "function") {
    console.error(
      "[provider.js] createProviderCatalogController() is not available. Check script load order."
    );
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
  const refreshButton = document.getElementById(
    "refresh-provider-capabilities-btn"
  );
  const loadModelsButton = document.getElementById("load-provider-models-btn");
  const modelCatalogStatus = document.getElementById(
    "provider-model-catalog-status"
  );
  const modelCatalogControls = document.getElementById(
    "provider-model-catalog-controls"
  );
  const modelCatalogEmpty = document.getElementById(
    "provider-model-catalog-empty"
  );
  const modelCatalogContainer = document.getElementById("provider-model-catalog");
  const modelSearchInput = document.getElementById("provider-model-search");
  const loadedOnlyWrapper = document.getElementById(
    "provider-model-loaded-filter-wrapper"
  );
  const loadedOnlyInput = document.getElementById("provider-model-loaded-only");
  const filterPillsContainer = document.getElementById(
    "provider-model-filter-pills"
  );
  const selectedModelSummary = document.getElementById(
    "provider-selected-model-summary"
  );
  const maxContextNote = document.getElementById("provider-max-context-note");
  const resetMaxContextButton = document.getElementById(
    "provider-reset-max-context-btn"
  );

  const providerId = form.dataset.providerId || "";
  const verificationStatusUrl = form.dataset.verificationStatusUrl || "";
  const modelCatalogUrl = form.dataset.modelCatalogUrl || "";
  const verificationPollIntervalMs = Number.parseInt(
    form.dataset.verificationPollIntervalMs || "2000",
    10
  );

  const state = {
    currentVerificationStatus: form.dataset.verificationStatus || "untested",
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
    labels: {
      saveConnection: "Save connection",
      saveProvider: "Save provider",
      loadModels: "Load models",
      refreshModels: "Refresh models",
      verifying: "Verifying…",
      verify: testButton
        ? testButton.textContent.trim()
        : "Run active verification",
    },
  };

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

  function isLmStudioProvider() {
    return (providerTypeInput ? providerTypeInput.value : "") === "lmstudio";
  }

  function updateSaveButtonLabel() {
    if (!saveButton) {
      return;
    }
    const label = hasSelectedModel()
      ? state.labels.saveProvider
      : state.labels.saveConnection;
    saveButton.innerHTML = `<i class="bi bi-check-lg me-1"></i>${label}`;
  }

  function updateTestButtonLabel() {
    if (!testButton) {
      return;
    }
    const label =
      state.currentVerificationStatus === "testing"
        ? state.labels.verifying
        : state.labels.verify;
    testButton.innerHTML = `<i class="bi bi-plug me-1"></i>${label}`;
  }

  function updateLoadButtonLabel() {
    if (!loadModelsButton) {
      return;
    }
    const label = state.catalogLoaded
      ? state.labels.refreshModels
      : state.labels.loadModels;
    loadModelsButton.innerHTML = `<i class="bi bi-arrow-repeat me-1"></i>${label}`;
  }

  function updateActionButtons() {
    updateSaveButtonLabel();
    updateTestButtonLabel();
    updateLoadButtonLabel();

    const verificationLocked = state.currentVerificationStatus === "testing";

    if (testButton) {
      testButton.disabled = verificationLocked || !hasSelectedModel();
    }

    if (refreshButton) {
      refreshButton.disabled = verificationLocked || !hasSelectedModel();
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

  const catalogController = createProviderCatalogController({
    refs: {
      providerTypeInput,
      modelInput,
      maxContextInput,
      loadModelsButton,
      modelCatalogStatus,
      modelCatalogControls,
      modelCatalogEmpty,
      modelCatalogContainer,
      modelSearchInput,
      loadedOnlyInput,
      filterPillsContainer,
      selectedModelSummary,
      maxContextNote,
      resetMaxContextButton,
      modelCatalogUrl,
    },
    state,
    helpers: {
      currentModelValue,
      parseInteger,
      providerSupportsModelCatalog,
      providerHasBeenSaved,
      hasUnsavedConnectionChanges,
      isLmStudioProvider,
    },
    callbacks: {
      updateActionButtons,
    },
  });

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

    if (apiKeyInput && nextDefaults.api_key_required === false) {
      apiKeyInput.required = false;
    }
  }

  async function pollVerificationStatus() {
    if (
      !verificationStatusUrl ||
      state.currentVerificationStatus !== "testing"
    ) {
      return;
    }

    try {
      const response = await fetch(verificationStatusUrl, {
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
      if (!payload || !payload.verification_status) {
        return;
      }

      if (payload.verification_status !== "testing") {
        window.location.reload();
      }
    } catch (_error) {
      // Keep polling silently while verification is in progress.
    }
  }

  function startVerificationPolling() {
    if (
      !verificationStatusUrl ||
      state.currentVerificationStatus !== "testing" ||
      state.pollingHandle
    ) {
      return;
    }

    state.pollingHandle = window.setInterval(
      pollVerificationStatus,
      Number.isFinite(verificationPollIntervalMs)
        ? verificationPollIntervalMs
        : 2000
    );
    pollVerificationStatus();
  }

  function bindEvents() {
    if (providerTypeInput) {
      providerTypeInput.addEventListener("change", () => {
        const previousProviderType = state.lastProviderType;
        syncProviderDefaults(previousProviderType);
        state.lastProviderType = providerTypeInput.value;
        catalogController.resetCatalogState({ keepStatus: false });
        updateActionButtons();
      });
    }

    [baseUrlInput, apiKeyInput].forEach((input) => {
      if (!input) {
        return;
      }
      input.addEventListener("input", () => {
        updateActionButtons();
        catalogController.renderCatalog();
      });
    });

    if (modelInput) {
      modelInput.addEventListener("input", () => {
        catalogController.syncSelectedCatalogItemFromModel();
        catalogController.renderCatalog();
        updateActionButtons();
      });
    }

    if (maxContextInput) {
      maxContextInput.addEventListener("input", () => {
        catalogController.syncManualMaxContextState();
        catalogController.updateMaxContextUi();
      });
    }

    if (resetMaxContextButton) {
      resetMaxContextButton.addEventListener("click", () => {
        if (state.suggestedMaxContextTokens == null || !maxContextInput) {
          return;
        }
        maxContextInput.value = String(state.suggestedMaxContextTokens);
        state.manualMaxContextOverride = false;
        catalogController.syncManualMaxContextState();
        catalogController.updateMaxContextUi();
      });
    }

    if (loadModelsButton) {
      loadModelsButton.addEventListener("click", () => {
        catalogController.loadModelCatalog();
      });
    }

    if (modelSearchInput) {
      modelSearchInput.addEventListener("input", () => {
        catalogController.renderCatalog();
      });
    }

    if (loadedOnlyInput) {
      loadedOnlyInput.addEventListener("change", () => {
        catalogController.renderCatalog();
      });
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
        state.currentVerificationStatus = "testing";
        updateActionButtons();
      }
    });
  }

  catalogController.syncSelectedCatalogItemFromModel();
  updateActionButtons();
  catalogController.renderFilterPills();
  catalogController.renderCatalog();
  bindEvents();
  startVerificationPolling();
})();
