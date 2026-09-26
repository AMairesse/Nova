// Run with node nova/tests/frontend_service_worker.test.js (no browser required).
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const listeners = {};
let offline = false;
let cacheBroken = false;
let cached = new Response('old JavaScript');
let requestedCacheMode;
const cache = {
  match: async () => cached,
  put: async (_req, response) => {
    if (cacheBroken) throw new Error('Cache full');
    cached = response;
  },
};
vm.runInNewContext(fs.readFileSync('nova/static/sw.js', 'utf8'), {
  self: { location: { origin: 'https://nova.test' }, addEventListener: (type, fn) => { listeners[type] = fn; } },
  caches: { open: async () => cache }, URL, Response, Date, console,
  fetch: async (_request, options) => {
    requestedCacheMode = options.cache;
    if (offline) throw new Error('Offline');
    return new Response('new JavaScript');
  },
});
function request(path, method = 'GET') {
  let response;
  listeners.fetch({ request: { url: `https://nova.test${path}`, method }, respondWith: (p) => { response = p; } });
  return response;
}
(async () => {
  assert.equal(await (await request('/static/js/streaming-manager.js')).text(), 'new JavaScript');
  assert.equal(requestedCacheMode, 'no-cache');
  assert.equal(await cached.clone().text(), 'new JavaScript');
  offline = true;
  assert.equal(await (await request('/static/js/streaming-manager.js')).text(), 'new JavaScript');
  cached = null;
  await assert.rejects(request('/static/js/missing.js'), /Offline/);
  offline = false;
  cacheBroken = true;
  assert.equal(await (await request('/static/js/streaming-manager.js')).text(), 'new JavaScript');
  assert.equal(request('/tasks/1/state/'), undefined);
  assert.equal(request('/static/test', 'POST'), undefined);
  console.log('frontend_service_worker.test.js: ok');
})().catch(error => { console.error(error); process.exitCode = 1; });
