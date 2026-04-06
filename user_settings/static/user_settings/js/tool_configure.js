/* user_settings/static/user_settings/js/tool_configure.js */

document.addEventListener("DOMContentLoaded", () => {
  //------------------------------------------------------------
  // 0. Generic conditional visibility for builtin config fields
  //    Driven by metadata attrs:
  //    - data-visible-if-field="enable_sending"
  //    - data-visible-if-equals="true" (JSON encoded)
  //------------------------------------------------------------
  const configForm = document.getElementById("configForm");
  if (configForm) {
    const wrappers = Array.from(
      configForm.querySelectorAll("[data-visible-if-field][data-visible-if-equals]")
    );

    // Map controller field name -> dependent wrappers
    const depsByController = new Map();

    const findWrapper = (el) =>
      el.closest(".mb-3") || el.closest(".form-check") || el.parentElement;

    const getFieldValue = (fieldEl) => {
      if (!fieldEl) return null;
      if (fieldEl.type === "checkbox") return !!fieldEl.checked;
      if (fieldEl.type === "number") {
        if (fieldEl.value === "") return null;
        const n = Number(fieldEl.value);
        return Number.isNaN(n) ? fieldEl.value : n;
      }
      return fieldEl.value;
    };

    const setRequired = (container, isVisible) => {
      if (!container) return;
      container.querySelectorAll("input, select, textarea").forEach((el) => {
        // Preserve original required state so we can restore it.
        if (el.dataset.originalRequired === undefined) {
          el.dataset.originalRequired = el.required ? "1" : "0";
        }
        if (!isVisible) {
          el.required = false;
        } else {
          el.required = el.dataset.originalRequired === "1";
        }
      });
    };

    const applyVisibility = (container, isVisible) => {
      if (!container) return;
      container.classList.toggle("d-none", !isVisible);
      setRequired(container, isVisible);
    };

    wrappers.forEach((depEl) => {
      const controllerName = depEl.dataset.visibleIfField;
      let expected;
      try {
        expected = JSON.parse(depEl.dataset.visibleIfEquals);
      } catch {
        expected = depEl.dataset.visibleIfEquals;
      }

      const container = findWrapper(depEl);
      if (!container || !controllerName) return;

      // Store expected value on the container (more stable than on the input).
      container.dataset.visibleIfField = controllerName;
      container.dataset.visibleIfEquals = JSON.stringify(expected);

      const arr = depsByController.get(controllerName) || [];
      arr.push(container);
      depsByController.set(controllerName, arr);
    });

    const evalContainer = (container) => {
      const controllerName = container.dataset.visibleIfField;
      if (!controllerName) return;

      const controller = configForm.querySelector(`[name="${CSS.escape(controllerName)}"]`);
      let expected;
      try {
        expected = JSON.parse(container.dataset.visibleIfEquals);
      } catch {
        expected = container.dataset.visibleIfEquals;
      }
      const actual = getFieldValue(controller);
      applyVisibility(container, actual === expected);
    };

    // Initial evaluation
    depsByController.forEach((containers) => containers.forEach(evalContainer));

    // Attach listeners (one per controller)
    depsByController.forEach((containers, controllerName) => {
      const controller = configForm.querySelector(`[name="${CSS.escape(controllerName)}"]`);
      if (!controller) return;
      controller.addEventListener("change", () => containers.forEach(evalContainer));
      controller.addEventListener("input", () => containers.forEach(evalContainer));
    });
  }

  //------------------------------------------------------------
  // 1. Connection mode switch (only for generic credentials)
  //------------------------------------------------------------
  const connectionModeSelect = document.querySelector('[name="connection_mode"]');
  let managedOAuthAdvancedVisible = false;
  const managedOAuthFieldNames = new Set(["client_id", "client_secret"]);
  const managedOAuthFieldsGroup = document.getElementById("oauthAdvancedFieldsGroup");
  const managedOAuthPanel = document.getElementById("managedOAuthPanel");
  const manualTestButton = document.getElementById("testBtn");
  const oauthConnectBtn = document.getElementById("oauthConnectBtn");
  const oauthVerifyBtn = document.getElementById("oauthVerifyBtn");
  const oauthAdvancedDetails = document.getElementById("oauthAdvancedDetails");
  const oauthAdvancedFieldsMount = document.getElementById("oauthAdvancedFieldsMount");
  const connectionModeHelpBlocks = Array.from(
    document.querySelectorAll("[data-connection-mode-help]")
  );

  if (managedOAuthFieldsGroup && oauthAdvancedFieldsMount) {
    oauthAdvancedFieldsMount.appendChild(managedOAuthFieldsGroup);
  }

  const applyManagedOAuthVisibility = () => {
    const managedModeSelected =
      connectionModeSelect && connectionModeSelect.value === "oauth_managed";

    if (managedOAuthPanel) {
      managedOAuthPanel.classList.toggle("d-none", !managedModeSelected);
    }
    if (manualTestButton) {
      manualTestButton.classList.toggle("d-none", managedModeSelected);
    }
    if (oauthConnectBtn) {
      oauthConnectBtn.classList.toggle("d-none", !managedModeSelected);
    }
    if (oauthVerifyBtn) {
      oauthVerifyBtn.classList.toggle("d-none", !managedModeSelected);
    }
    if (oauthAdvancedDetails) {
      oauthAdvancedDetails.classList.toggle("d-none", !managedModeSelected);
    }

    if (managedOAuthFieldsGroup) {
      managedOAuthFieldsGroup.classList.toggle(
        "d-none",
        !(managedModeSelected && managedOAuthAdvancedVisible)
      );
      managedOAuthFieldsGroup.querySelectorAll("input").forEach((input) => {
        input.required = false;
      });
    }
  };
  const applyConnectionModeHelp = () => {
    if (!connectionModeSelect) return;
    connectionModeHelpBlocks.forEach((block) => {
      block.classList.toggle(
        "d-none",
        block.dataset.connectionModeHelp !== connectionModeSelect.value
      );
    });
  };
  if (connectionModeSelect) {
    const mapping = {
      none: [],
      basic: ["username", "password"],
      token: ["token"],
      api_key: ["token", "api_key_name", "api_key_in"],
      custom: [],
      oauth_managed: [],
    };
    const hideAll = () =>
      document.querySelectorAll("[data-auth-field]").forEach((c) => {
        if (managedOAuthFieldNames.has(c.dataset.authField || "")) {
          return;
        }
        const container = c.closest('.mb-3') || c.parentElement;
        if (container) container.style.display = "none";
        c.querySelectorAll("input").forEach((i) => {
          i.required = false;
        });
      });
    const show = (names) =>
      names.forEach((n) => {
        const c = document.querySelector(`[data-auth-field="${n}"]`);
        if (c) {
          const container = c.closest('.mb-3') || c.parentElement;
          if (container) container.style.display = "";
        }
      });
    const toggle = () => {
      hideAll();
      show(mapping[connectionModeSelect.value] || []);
      applyConnectionModeHelp();
      applyManagedOAuthVisibility();
    };
    toggle();
    connectionModeSelect.addEventListener("change", toggle);
  }

  if (oauthAdvancedDetails) {
    managedOAuthAdvancedVisible = oauthAdvancedDetails.open;
    applyManagedOAuthVisibility();
    oauthAdvancedDetails.addEventListener("toggle", () => {
      managedOAuthAdvancedVisible = oauthAdvancedDetails.open;
      applyManagedOAuthVisibility();
    });
  } else {
    applyManagedOAuthVisibility();
  }

  //------------------------------------------------------------
  // 2. Shared test/verify actions
  //------------------------------------------------------------
  const resultBox = document.getElementById("testResult");
  const runConnectionAction = async ({ button, action, pendingText }) => {
    if (!button || !resultBox) return;
    resultBox.className = "alert alert-info";
    resultBox.textContent = pendingText;
    resultBox.classList.remove("d-none");

    const url = button.dataset.testUrl;
    const formData = new FormData(document.getElementById("configForm"));
    formData.set("connection_action", action);

    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": formData.get("csrfmiddlewaretoken") },
        body: formData,
      });
      const data = await resp.json();
      if (data.status === "oauth_redirect" && data.authorization_url) {
        window.location.href = data.authorization_url;
        return;
      }
      resultBox.textContent = data.message || data.status;
      resultBox.className =
        "alert " + (data.status === "success" ? "alert-success" : "alert-danger");
    } catch (e) {
      resultBox.textContent = e;
      resultBox.className = "alert alert-danger";
    }
  };

  const testBtn = document.getElementById("testBtn");
  if (testBtn) {
    testBtn.addEventListener("click", async () => {
      await runConnectionAction({
        button: testBtn,
        action: "test",
        pendingText: "Testing…",
      });
    });
  }

  //------------------------------------------------------------
  // 3. Managed OAuth actions
  //------------------------------------------------------------
  if (oauthConnectBtn) {
    oauthConnectBtn.addEventListener("click", async () => {
      await runConnectionAction({
        button: oauthConnectBtn,
        action: "connect_oauth",
        pendingText: "Preparing OAuth…",
      });
    });
  }

  if (oauthVerifyBtn) {
    oauthVerifyBtn.addEventListener("click", async () => {
      await runConnectionAction({
        button: oauthVerifyBtn,
        action: "verify",
        pendingText: "Verifying connection…",
      });
    });
  }
});
