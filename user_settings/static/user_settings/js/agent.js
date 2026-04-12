/* user_settings/static/user_settings/js/agent.js */
(function () {
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
    const standardCapabilities = Array.from(
      document.querySelectorAll('input[name="standard_capabilities"]:checked')
    ).some((input) => `${input.value || ""}`.trim());
    const connectionTools = Array.from(
      document.querySelectorAll('input[name="connection_tools"]:checked')
    ).some((input) => `${input.value || ""}`.trim());
    const searchBackend = `${document.getElementById("id_search_backend")?.value || ""}`.trim();
    const pythonBackend = `${document.getElementById("id_python_backend")?.value || ""}`.trim();
    const selectedAgentTools = Array.from(
      document.querySelectorAll('input[name="agent_tools"]:checked')
    ).some((input) => `${input.value || ""}`.trim());

    return Boolean(
      standardCapabilities ||
      connectionTools ||
      searchBackend ||
      pythonBackend ||
      selectedAgentTools
    );
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
    isTool.addEventListener("change", syncProviderToolWarning);
    syncToolDescriptionVisibility();
  }

  if (providerSelect) {
    providerSelect.addEventListener("change", syncProviderToolWarning);
  }
  document.querySelectorAll('input[name="standard_capabilities"]').forEach((input) => {
    input.addEventListener("change", syncProviderToolWarning);
  });
  document.querySelectorAll('input[name="connection_tools"]').forEach((input) => {
    input.addEventListener("change", syncProviderToolWarning);
  });
  document.querySelectorAll('input[name="agent_tools"]').forEach((input) => {
    input.addEventListener("change", syncProviderToolWarning);
  });
  ["id_search_backend", "id_python_backend"].forEach((id) => {
    const select = document.getElementById(id);
    if (select) {
      select.addEventListener("change", syncProviderToolWarning);
    }
  });

  syncProviderToolWarning();
})();
