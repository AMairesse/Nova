/* Run with node nova/tests/frontend_provider_catalog.test.js. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const requests = [];
let resolveRequest;
const window = { DOMUtils: { csrfFetch: async (url, options) => {
  requests.push({ url, options });
  return new Promise(resolve => { resolveRequest = resolve; });
} } };
const context = { window, FormData, fetch: () => { throw new Error('New connection must use POST preview'); } };
vm.createContext(context);
vm.runInContext(fs.readFileSync('user_settings/static/user_settings/js/provider-catalog.js', 'utf8'), context);
const values = { provider_type: 'openrouter', api_key: 'draft-key', base_url: '', csrfmiddlewaretoken: 'csrf' };
const state = { activeFilters: new Set(), catalogItems: [], catalogLoaded: false };
const controller = window.NovaApp.Modules.createProviderCatalogController({
  refs: { loadModelsButton: {}, modelCatalogPreviewUrl: '/preview/',
    form: { dataset: { providerId: '' }, elements: { namedItem: name => ({ value: values[name] }) } } },
  state,
  helpers: { providerSupportsModelCatalog: () => true, providerHasBeenSaved: () => false,
    hasUnsavedConnectionChanges: () => false, currentModelValue: () => '', parseInteger: x => parseInt(x),
    isLmStudioProvider: () => false },
  callbacks: { updateActionButtons() {} },
});
(async () => {
  const load = controller.loadModelCatalog();
  assert.strictEqual(requests[0].options.method, 'POST');
  assert.strictEqual(requests[0].url, '/preview/');
  assert.strictEqual(requests[0].options.body.get('api_key'), 'draft-key');
  resolveRequest({ ok: true, json: async () => ({ models: [{ id: 'model-a' }] }) });
  await load;
  assert.strictEqual(state.catalogLoaded, true);
  assert.strictEqual(state.catalogItems[0].id, 'model-a');

  const staleLoad = controller.loadModelCatalog();
  controller.resetCatalogState();
  resolveRequest({ ok: true, json: async () => ({ models: [{ id: 'old-connection-model' }] }) });
  await staleLoad;
  assert.strictEqual(state.catalogLoaded, false, 'changed connection must discard the pending response');
  assert.strictEqual(state.catalogItems.length, 0);
  assert.strictEqual(state.catalogLoading, false);
  console.log('Provider catalog scenarios passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
