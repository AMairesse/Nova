/* static/js/user_config.js */
/* ------------------------------------------------------------------------
 * 1.  Provider-specific extra fields
 * --------------------------------------------------------------------- */
function getDefaultMaxTokens(providerType) {
  if (['ollama', 'lmstudio'].includes(providerType)) return 4096;
  if (['openai', 'mistral'].includes(providerType)) return 100000;
  return 4096;  // Default général
}

function getProviderFields(providerType) {
  const commonField = `
    <div class="mb-3">
      <label class="form-label">${gettext("Max context tokens")}</label>
      <input class="form-control" name="max_context_tokens" type="number" min="512" value="${getDefaultMaxTokens(providerType)}" required>
      <small class="form-text text-muted">${gettext("Max tokens for this provider's context (e.g., 4096 for small models).")}</small>
    </div>`;

  const fields = {
    mistral: () => `
      <div class="mb-3">
        <label class="form-label">${gettext("Model")}</label>
        <input class="form-control" name="model" placeholder="mistral-medium-latest" required>
      </div>
      <div class="mb-3">
        <label class="form-label">${gettext("API key")}</label>
        <input class="form-control" name="api_key" type="password">
        <small class="form-text text-muted keep-api-msg d-none">
          ${gettext("A key is already registered ; leave empty to keep it.")}
        </small>
      </div>
      ${commonField}`,

    openai: () => `
      <div class="mb-3">
        <label class="form-label">${gettext("Model")}</label>
        <input class="form-control" name="model" placeholder="gpt-4o" required>
      </div>
      <div class="mb-3">
        <label class="form-label">${gettext("API key")}</label>
        <input class="form-control" name="api_key" type="password">
        <small class="form-text text-muted keep-api-msg d-none">
          ${gettext("A key is already registered ; leave empty to keep it.")}
        </small>
      </div>
      <div class="mb-3">
        <label class="form-label">${gettext("Base URL (optional)")}</label>
        <input class="form-control" name="base_url" placeholder="https://api.openai.com/v1">
      </div>
      ${commonField}`,

    ollama: () => `
      <div class="mb-3">
        <label class="form-label">${gettext("Model")}</label>
        <input class="form-control" name="model" placeholder="llama3">
      </div>
      <div class="mb-3">
        <label class="form-label">${gettext("Base URL")}</label>
        <input class="form-control" name="base_url" value="http://localhost:11434">
      </div>
      ${commonField}`,

    lmstudio: () => `
      <div class="mb-3">
        <label class="form-label">${gettext("Model")}</label>
        <input class="form-control" name="model" placeholder="phi3">
      </div>
      <div class="mb-3">
        <label class="form-label">${gettext("Base URL")}</label>
        <input class="form-control" name="base_url" value="http://localhost:1234/v1">
      </div>
      ${commonField}`
  };

  return (fields[providerType] || (() => ''))();  // Empty if unknown type
}

/* Simple helper */
function injectFields(selectElt, targetId) {
  const div = document.getElementById(targetId);
  div.innerHTML = getProviderFields(selectElt.value);
}

/* ------------------------------------------------------------------------
 * 2.  AGENT EDIT MODAL
 * --------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.edit-agent-btn').forEach(btn => {
    btn.addEventListener('click', function () {
      /* Data extraction */
      const id          = this.dataset.id;
      const name        = this.dataset.name;
      const providerId  = this.dataset.llmProviderId;
      const prompt      = this.dataset.systemPrompt || '';
      const toolDesc    = this.dataset.toolDescription || '';
      const isTool      = this.dataset.isTool === 'True';
      const toolsStr    = this.dataset.tools;
      const agentToolsStr = this.dataset.agentTools;

      /* Fill the form */
      document.getElementById('editAgentId').value   = id;
      document.getElementById('editAgentName').value = name;
      document.getElementById('editLlmProvider').value = providerId;
      document.getElementById('editSystemPrompt').value = prompt;
      document.getElementById('editToolDescription').value = toolDesc;
      document.getElementById('editIsToolCheckbox').checked = isTool;
      document.getElementById('editAgentForm').action = `/agent/edit/${id}/`;
      document.getElementById('editToolDescriptionWrapper').style.display = isTool ? 'block' : 'none';
      document.getElementById('editToolDescription').required = isTool;

      /* Checkboxes (tools) */
      document.querySelectorAll('#editToolsSelection input[type="checkbox"]').forEach(cb => cb.checked = false);
      if (toolsStr) {
        toolsStr.split(',').filter(Boolean).forEach(tid => {
          const cb = document.querySelector(`#editToolsSelection [value="${tid}"]`);
          if (cb) cb.checked = true;
        });
      }

      /* Checkboxes (agents as tools) */
      document.querySelectorAll('#editAgentToolsSelection input[type="checkbox"]').forEach(cb => cb.checked = false);
      if (agentToolsStr) {
        agentToolsStr.split(',').filter(Boolean).forEach(aid => {
          const cb = document.querySelector(`#editAgentToolsSelection [value="${aid}"]`);
          if (cb) cb.checked = true;
        });
      }

      /* Inject provider-specific fields */
      injectFields(document.getElementById('editLlmProvider'), 'editProviderFields');

      new bootstrap.Modal(document.getElementById('editAgentModal')).show();
    });
  });

  /* Refresh provider-specific fields when provider changes in the edit form */
  document.getElementById('editLlmProvider').addEventListener('change', function () {
    injectFields(this, 'editProviderConfigFields');
  });

  /* Toggle tool description in create modal */
  document.getElementById('createIsToolCheckbox').addEventListener('change', function() {
    document.getElementById('createToolDescriptionWrapper').style.display = this.checked ? 'block' : 'none';
    document.getElementById('toolDescription').required = this.checked;
  });

  /* Toggle tool description in edit modal */
  document.getElementById('editIsToolCheckbox').addEventListener('change', function() {
    document.getElementById('editToolDescriptionWrapper').style.display = this.checked ? 'block' : 'none';
    document.getElementById('editToolDescription').required = this.checked;
  });

  /* Simple client-side validation: prevent submit if description empty when is_tool */
  document.querySelectorAll('[action*="create_agent"], #editAgentForm').forEach(form => {
    form.addEventListener('submit', function(e) {
      const isTool = this.querySelector('[name="is_tool"]').checked;
      const desc = this.querySelector('[name="tool_description"]').value.trim();
      if (isTool && !desc) {
        e.preventDefault();
        alert(gettext("Tool description is required when using as tool."));
      }
    });
  });

  /* Langfuse fields toggle (using Django's default ID for the checkbox) */
  const allowCheckbox = document.getElementById('id_allow_langfuse');
  if (allowCheckbox) {
    allowCheckbox.addEventListener('change', function() {
      document.getElementById('langfuse-fields').style.display = this.checked ? 'block' : 'none';
    });
  }
});

/* ------------------------------------------------------------------------
 * 3.  PROVIDER CREATION + EDIT MODALS
 * --------------------------------------------------------------------- */
document.addEventListener('DOMContentLoaded', function() {
  /* Creation modal */
  const providerTypeSelect = document.getElementById('providerType');
  const createProviderBtn  = document.getElementById('createProviderBtn');

  providerTypeSelect.addEventListener('change', function () {
    injectFields(this, 'providerConfigFields');
    createProviderBtn.disabled = (this.value === '');
    const maxInput = document.querySelector('#providerConfigFields [name="max_context_tokens"]');
    if (maxInput) maxInput.value = getDefaultMaxTokens(this.value);
  });

  /* Edit modal – open & fill */
  document.querySelectorAll('.edit-provider-btn').forEach(button => {
    button.addEventListener('click', function() {
      const id   = this.dataset.id;
      const name = this.dataset.name;
      const type = this.dataset.providerType;
      const model    = this.dataset.model;
      const base_url = this.dataset.baseUrl;
      const config   = this.dataset.config;

      document.getElementById('editProviderId').value   = id;
      document.getElementById('editProviderName').value = name;
      document.getElementById('editProviderType').value = type;
      document.getElementById('editProviderForm').action = `/provider/edit/${id}/`;

      injectFields(document.getElementById('editProviderType'), 'editProviderConfigFields');

      /* Populate dynamic fields */
      const cfgDiv = document.getElementById('editProviderConfigFields');
      const modelInput    = cfgDiv.querySelector('[name="model"]');
      const baseUrlInput  = cfgDiv.querySelector('[name="base_url"]');
      const apiKeyInput   = cfgDiv.querySelector('[name="api_key"]');

      if (modelInput)   modelInput.value   = model;
      if (baseUrlInput) baseUrlInput.value = base_url || '';

      if (apiKeyInput) {                 // key already stored
        apiKeyInput.placeholder = '(…)';
        const msg = cfgDiv.querySelector('.keep-api-msg');
        if (msg) msg.classList.remove('d-none');
      }

      /* Additional JSON config */
      let cfg = {};
      try { cfg = JSON.parse(config); } catch (_) {}
      for (const [k,v] of Object.entries(cfg)) {
        const inp = cfgDiv.querySelector(`[name="${k}"]`);
        if (inp) inp.value = v;
      }

      const maxTokens = this.dataset.maxTokens;
      const maxInput = document.querySelector('#editProviderConfigFields [name="max_context_tokens"]');
      if (maxInput && maxTokens) maxInput.value = maxTokens;

      new bootstrap.Modal(document.getElementById('editProviderModal')).show();
    });
  });

  /* Refresh fields live in the edit modal */
  document.getElementById('editProviderType').addEventListener('change', function () {
    injectFields(this, 'editProviderConfigFields');
    const maxInput = document.querySelector('#editProviderConfigFields [name="max_context_tokens"]');
    if (maxInput) maxInput.value = getDefaultMaxTokens(this.value);
  });

  /* Activate correct tab based on URL (?tab=…) */
  const urlParams = new URLSearchParams(window.location.search);
  const activeTab = urlParams.get('tab');
  if (activeTab) {
    const tabElement = document.getElementById(activeTab + '-tab');
    if (tabElement) new bootstrap.Tab(tabElement).show();
  }
});
