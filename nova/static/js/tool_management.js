/* nova/static/js/tool_management.js */
document.addEventListener("DOMContentLoaded", () => {
  /* ------------------------------------------------------------------ */
  /*  Helper that toggles dynamic form fields according to tool type    */
  /* ------------------------------------------------------------------ */
  function toggleToolFields(selectElement) {
    const toolType = selectElement.value;
    const toolId = selectElement.getAttribute("data-tool-id") || "new";
    const dynamicFieldsContainer = selectElement
      .closest(".modal-body")
      ?.querySelector(`.dynamic-fields[data-tool-id="${toolId}"]`);
    
    if (!dynamicFieldsContainer) return;

    // Hide all field groups and remove required attributes
    const fieldGroups = dynamicFieldsContainer.querySelectorAll(".field-group");
    const formElements = dynamicFieldsContainer.querySelectorAll("input, select, textarea");
    
    fieldGroups.forEach(group => group.style.display = "none");
    formElements.forEach(element => element.removeAttribute("required"));

    // Show relevant fields based on tool type
    switch (toolType) {
      case "builtin":
        showBuiltinFields(dynamicFieldsContainer);
        break;
      case "api":
      case "mcp":
        showApiMcpFields(dynamicFieldsContainer, toolType);
        break;
    }
  }

  function showBuiltinFields(container) {
    const builtinFields = container.querySelector(".builtin-fields");
    if (builtinFields) {
      builtinFields.style.display = "block";
      const subtypeSelect = builtinFields.querySelector('select[name="tool_subtype"]');
      if (subtypeSelect) subtypeSelect.setAttribute("required", "required");
    }
  }

  function showApiMcpFields(container, toolType) {
    const apiMcpFields = container.querySelector(".api-mcp-fields");
    if (apiMcpFields) {
      apiMcpFields.style.display = "block";
      
      // Set required fields
      const requiredFields = [
        'input[name="name"]',
        'textarea[name="description"]',
        'input[name="endpoint"]'
      ];
      
      requiredFields.forEach(selector => {
        const field = apiMcpFields.querySelector(selector);
        if (field) field.setAttribute("required", "required");
      });
    }
    
    if (toolType === "api") {
      const apiFields = container.querySelector(".api-fields");
      if (apiFields) apiFields.style.display = "block";
    } else if (toolType === "mcp") {
      const mcpFields = container.querySelector(".mcp-fields");
      if (mcpFields) mcpFields.style.display = "block";
    }
  }

  // Initialize tool type selects
  const toolTypeSelects = document.querySelectorAll(".tool-type-select");
  toolTypeSelects.forEach(select => {
    select.addEventListener("change", () => toggleToolFields(select));
    if (select.value) toggleToolFields(select);
  });

  /* Auth-type toggles -------------------------------------------------- */
  document.querySelectorAll(".auth-type-select").forEach((sel) => {
    sel.addEventListener("change", function () {
      const modalContent = this.closest(".modal-content");
      modalContent
        .querySelectorAll(".auth-field")
        .forEach((f) => (f.style.display = "none"));
      if (this.value !== "none") {
        modalContent
          .querySelectorAll(`.${this.value}-auth`)
          .forEach((f) => (f.style.display = "block"));
      }
    });
    sel.dispatchEvent(new Event("change"));
  });

  /* JSON editors – validate & pretty-print ---------------------------- */
  document.querySelectorAll(".json-editor").forEach((ed) => {
    ed.addEventListener("blur", function () {
      const errorDiv = this.parentElement.querySelector(".json-error");
      try {
        const txt = this.value.trim();
        if (txt) this.value = JSON.stringify(JSON.parse(txt), null, 2);
        if (errorDiv) errorDiv.style.display = "none";
      } catch (e) {
        if (errorDiv) {
          errorDiv.textContent = `${gettext("Invalid JSON: ")}${e.message}`;
          errorDiv.style.display = "block";
        }
      }
    });
  });

  /* “Test connection” buttons ---------------------------------------- */
  document.querySelectorAll(".test-connection-btn").forEach((btn) => {
    btn.addEventListener("click", function (e) {
      e.preventDefault();
      const toolId = this.getAttribute("data-tool-id");
      const resultDiv = document.getElementById(
        `connection-test-result-${toolId}`
      );
      const form = this.closest("form");
      if (!resultDiv || !form) return;

      resultDiv.innerHTML =
        '<div class="spinner-border spinner-border-sm text-primary" role="status"></div> ' +
        gettext("Testing…");
      resultDiv.className = "alert alert-info mt-2";
      resultDiv.style.display = "block";

      const formData = new FormData(form);
      DOMUtils.csrfFetch(`/tool/test-connection/${toolId}/`, {
        method: "POST",
        body: formData,
      })
        .then((r) => r.json())
        .then((data) => {
          resultDiv.className = "alert mt-2";
          if (data.status === "success") {
            resultDiv.classList.add("alert-success");
            resultDiv.innerHTML = `<i class="bi bi-check-circle-fill"></i> ${data.message}`;
            /* Optional extra info (calendars / tools) */
            if (data.calendars?.length) {
              let html = `<div class="mt-2"><strong>${gettext(
                "Available calendars:"
              )}</strong><ul class="mb-0">`;
              data.calendars.forEach((c) => (html += `<li>${c.name}</li>`));
              html += "</ul></div>";
              resultDiv.innerHTML += html;
            }
            if (data.tools?.length) {
              let html = `<details class="mt-2"><summary><strong>${gettext(
                "MCP tools available:"
              )} (${data.tools.length})</strong></summary>`;
              data.tools.forEach((t) => {
                html += `
                <div class="border rounded p-2 my-2">
                  <h6 class="mb-1">${t.name}</h6>
                  <p class="small text-muted">${t.description || ""}</p>
                  <pre class="bg-light p-2"><code>${JSON.stringify(
                    t.input_schema || {},
                    null,
                    2
                  )}</code></pre>
                  <small class="text-muted">${gettext("input_schema")}</small>
                  <pre class="bg-light p-2"><code>${JSON.stringify(
                    t.output_schema || {},
                    null,
                    2
                  )}</code></pre>
                  <small class="text-muted">${gettext("output_schema")}</small>
                </div>`;
              });
              html += "</details>";
              resultDiv.innerHTML += html;
            }
          } else {
            resultDiv.classList.add("alert-danger");
            resultDiv.innerHTML = `<i class="bi bi-exclamation-triangle-fill"></i> ${gettext(
              "Error"
            )}: ${data.message || gettext("Unknown error")}`;
          }
        })
        .catch((err) => {
          console.error("Detailed error:", err);
          resultDiv.className = "alert alert-danger mt-2";
          resultDiv.innerHTML =
            `<i class="bi bi-exclamation-triangle-fill"></i> ${gettext(
              "Network error: "
            )}` + err.message;
        });
    });
  });

  /* Hide connection result when modal closes ------------------------- */
  document.querySelectorAll(".modal").forEach((m) =>
    m.addEventListener("hidden.bs.modal", function () {
      this.querySelectorAll(".json-error, .alert").forEach((msg) => {
        if (msg.id?.includes("connection-test-result"))
          msg.style.display = "none";
      });
    })
  );
});
