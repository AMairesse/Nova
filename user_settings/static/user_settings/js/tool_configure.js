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
  // 1. Auth-type field switch (only for generic credentials)
  //------------------------------------------------------------
  const authSelect = document.querySelector('[name="auth_type"]');
  if (authSelect) {
    const mapping = {
      none: [],
      basic: ["username", "password"],
      token: ["token", "token_type"],
      api_key: ["token"],
      oauth: ["client_id", "client_secret"],
      custom: [],
    };
    const hideAll = () =>
      document.querySelectorAll("[data-auth-field]").forEach((c) => {
        const container = c.closest('.mb-3') || c.parentElement;
        if (container) container.style.display = "none";
        c.querySelectorAll("input").forEach((i) => (i.required = false));
      });
    const show = (names) =>
      names.forEach((n) => {
        const c = document.querySelector(`[data-auth-field="${n}"]`);
        if (c) {
          const container = c.closest('.mb-3') || c.parentElement;
          if (container) container.style.display = "";
          const i = c.querySelector("input");
          if (i) i.required = true;
        }
      });
    const toggle = () => {
      hideAll();
      show(mapping[authSelect.value] || []);
    };
    toggle();
    authSelect.addEventListener("change", toggle);
  }

  //------------------------------------------------------------
  // 2. Test connection
  //------------------------------------------------------------
  const testBtn = document.getElementById("testBtn");
  if (testBtn) {
    const resultBox = document.getElementById("testResult");
    testBtn.addEventListener("click", async () => {
      resultBox.className = "alert alert-info";
      resultBox.textContent = "Testing…";
      resultBox.classList.remove("d-none");

      const url = testBtn.dataset.testUrl;
      const formData = new FormData(document.getElementById("configForm"));

      try {
        const resp = await fetch(url, {
          method: "POST",
          headers: { "X-CSRFToken": formData.get("csrfmiddlewaretoken") },
          body: formData,
        });
        const data = await resp.json();
        resultBox.textContent = data.message || data.status;
        resultBox.className =
          "alert " + (data.status === "success" ? "alert-success" : "alert-danger");
      } catch (e) {
        resultBox.textContent = e;
        resultBox.className = "alert alert-danger";
      }
    });
  }
});
