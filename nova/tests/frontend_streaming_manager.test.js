/* Deterministic browser-free checks for streaming-manager.js. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

let now = 0;
let nextTimer = 1;
const timers = new Map();
const timeout = (fn, delay = 0) => { const id = nextTimer++; timers.set(id, { fn, at: now + delay, interval: 0 }); return id; };
const interval = (fn, delay) => { const id = nextTimer++; timers.set(id, { fn, at: now + delay, interval: delay }); return id; };
const clearTimer = (id) => timers.delete(id);
const tick = async (ms) => {
  now += ms;
  let ran;
  do {
    ran = false;
    for (const [id, timer] of [...timers]) {
      if (timer.at > now) continue;
      ran = true;
      if (timer.interval) timer.at += timer.interval; else timers.delete(id);
      timer.fn();
    }
    await Promise.resolve();
  } while (ran);
};

class Element {
  constructor(id = '') {
    this.id = id;
    this.dataset = id ? { messageId: id } : {};
    this.parentNode = null;
    this.isConnected = false;
    this.innerHTML = '';
    this.children = new Map();
    this.classList = { values: new Set(), add: (x) => this.classList.values.add(x), contains: (x) => this.classList.values.has(x), toggle: (x, on) => on ? this.classList.values.add(x) : this.classList.values.delete(x) };
  }
  querySelector(selector) {
    if (!this.children.has(selector)) this.children.set(selector, new Element());
    return this.children.get(selector);
  }
  replaceWith(element) {
    this.isConnected = false;
    element.parentNode = document;
    element.isConnected = true;
    const index = messageNodes.indexOf(this);
    if (index >= 0) messageNodes[index] = element;
  }
  remove() { this.isConnected = false; }
}

const messageNodes = [];
const traceButton = new Element();
const document = {
  visibilityState: 'visible',
  addEventListener() {},
  querySelector() { return null; },
  querySelectorAll(selector) { return selector.includes('data-message-id') ? messageNodes : []; },
  getElementById(id) {
    if (id === 'task-progress-trace-btn') return traceButton;
    return messageNodes.find((node) => node.id === id) || null;
  },
  dispatchEvent() {},
};

const sockets = [];
class FakeWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static CONNECTING = 0;
  constructor(url) { this.url = url; this.readyState = FakeWebSocket.CONNECTING; this.sent = []; this.closedWith = null; sockets.push(this); }
  open() { this.readyState = FakeWebSocket.OPEN; this.onopen?.(); }
  send(payload) { this.sent.push(JSON.parse(payload)); }
  close(code) { this.closedWith = code; this.readyState = FakeWebSocket.CLOSED; this.onclose?.({ code: code || 1000 }); }
  receive(data) { this.onmessage?.({ data: JSON.stringify(data) }); }
}

const fetchQueue = [];
const fetchStub = () => {
  const next = fetchQueue.shift();
  if (next instanceof Error) return Promise.reject(next);
  return Promise.resolve({ ok: true, json: () => Promise.resolve(next) });
};
const window = {
  location: { protocol: 'http:', host: 'testserver' }, NovaApp: { urls: {} }, addEventListener() {},
  setTimeout: timeout, clearTimeout: clearTimer, setInterval: interval, clearInterval: clearTimer,
};
const warnings = [];
const context = { window, document, AbortController, WebSocket: FakeWebSocket, CustomEvent: class {}, gettext: (x) => x, fetch: fetchStub,
  console: { ...console, warn: (...args) => warnings.push(args) } };
context.setTimeout = timeout;
context.clearTimeout = clearTimer;
context.setInterval = interval;
context.clearInterval = clearTimer;
vm.createContext(context);
vm.runInContext(fs.readFileSync('nova/static/js/streaming-manager.js', 'utf8'), context);
context.window.MessageRenderer = {
  createMessageElement(data) {
    const element = new Element(String(data.id));
    element.id = `message-${data.id}`;
    element.dataset.messageId = String(data.id);
    return element;
  },
};

const reset = () => {
  assert.strictEqual(warnings.length, 0, 'unexpected websocket handler error');
  now = 0;
  timers.clear();
  fetchQueue.length = 0;
  sockets.length = 0;
  messageNodes.length = 0;
};

const makeManager = () => {
  const manager = new context.window.StreamingManager();
  manager.statePollMs = 10;
  manager.messageManager = {
    currentThreadId: '7',
    appendMessage(element) { element.parentNode = document; element.isConnected = true; if (element.dataset.messageId && !messageNodes.includes(element)) messageNodes.push(element); },
    updateCompactLinkVisibility() {}, followBottomDuringLayout() {}, isNearBottom: () => true,
    scheduleExecutionTraceRefresh() {}, showToast() {},
  };
  return manager;
};
const response = (status, messages = []) => ({ type: 'task_snapshot', task_id: 'task', thread_id: '7', status, current_response: '', updated_at: new Date(now).toISOString(), messages, interactions: [] });
const flush = async () => { for (let index = 0; index < 8; index += 1) await Promise.resolve(); };

(async () => {
  reset();
  let manager = makeManager();
  fetchQueue.push(response('RUNNING'));
  manager.registerStream('task', { id: 'task' });
  await flush();
  sockets.at(-1).open();
  sockets.at(-1).receive({ ...response('COMPLETED', [{ id: 101, actor: 'AGT', text: 'done' }]), task_id: 'task' });
  await flush();
  assert.strictEqual(manager.activeStreams.has('task'), false, 'completed initial snapshot must clean up');
  assert.strictEqual(messageNodes.some((node) => node.dataset.messageId === '101'), true, 'snapshot message rendered');

  reset();
  manager = makeManager();
  fetchQueue.push(response('RUNNING'), response('COMPLETED', [{ id: 102, actor: 'AGT', text: 'recovered' }]));
  manager.registerStream('poll-task', { id: 'poll-task' });
  await flush();
  assert.strictEqual(manager.activeStreams.has('poll-task'), true);
  await tick(10); await flush();
  assert.strictEqual(manager.activeStreams.has('poll-task'), false, 'poll must recover missed completion');

  reset();
  manager = makeManager();
  fetchQueue.push(response('RUNNING'));
  manager.registerStream('socket-task', { id: 'socket-task' });
  const socketCountBeforeReconnect = sockets.length;
  const firstSocket = sockets.at(-1); firstSocket.open(); firstSocket.close(1006);
  await tick(500); await flush();
  assert.strictEqual(sockets.length, socketCountBeforeReconnect + 1, 'disconnect must reconnect exactly once');
  const heartbeatSocket = sockets.at(-1); heartbeatSocket.open();
  await tick(30000); await tick(10000);
  assert.strictEqual(heartbeatSocket.readyState, FakeWebSocket.CLOSED, 'heartbeat timeout must close the socket');
  assert.notStrictEqual(heartbeatSocket.closedWith, 1006, 'heartbeat must not send invalid client close code');

  reset();
  manager = makeManager();
  fetchQueue.push(response('RUNNING'));
  manager.registerStream('old-task', { id: 'old-task' });
  const oldSocket = sockets.at(-1); manager.onThreadChanged('8');
  oldSocket.receive({ type: 'response_chunk', thread_id: '7', chunk: '<p>stale</p>' });
  assert.strictEqual(manager.activeStreams.has('old-task'), false, 'old thread task cleaned');

  reset();
  manager = makeManager();
  const liveElement = new Element('stream-stale-task');
  liveElement.isConnected = true;
  liveElement.classList.add('streaming');
  liveElement.querySelector('.streaming-content').innerHTML = '<p>live</p>';
  manager.activeStreams.set('stale-task', { taskId: 'stale-task', threadId: '7', revision: 0, updatedAt: 0, localEventCounter: 1, hasLiveContent: true, lastChunk: '<p>live</p>', element: liveElement, placeholderActive: true, seenMessageIds: new Set() });
  manager.applyTaskSnapshot('stale-task', { thread_id: '7', current_response: '<p>old</p>', updated_at: new Date(now + 1000).toISOString() }, { requestedEventCounter: 1 });
  assert.strictEqual(manager.activeStreams.get('stale-task').lastChunk, '<p>live</p>', 'stale snapshot cannot overwrite live chunk');

  reset();
  manager = makeManager();
  fetchQueue.push({ ...response('RUNNING'), updated_at: new Date(1000).toISOString() });
  manager.registerStream('live-task', { id: 'live-task' });
  await flush();
  const liveSocket = sockets.at(-1); liveSocket.open();
  liveSocket.receive({ type: 'response_chunk', thread_id: '7', chunk: '<p>live response</p>' });
  fetchQueue.push({ ...response('RUNNING'), updated_at: new Date(2000).toISOString(), current_response: '<p>persisted lag</p>' });
  await tick(10); await flush();
  assert.strictEqual(manager.activeStreams.get('live-task').lastChunk, '<p>live response</p>', 'poll snapshot cannot regress a live response');

  reset();
  manager = makeManager();
  fetchQueue.push(response('RUNNING'));
  manager.registerStream('retry-task', { id: 'retry-task' });
  await flush();
  const duplicate = { id: 104, actor: 'AGT', text: 'duplicate' };
  manager.onNewMessage(duplicate, '7', 'retry-task');
  manager.onNewMessage(duplicate, '7', 'retry-task');
  assert.strictEqual(messageNodes.filter((node) => node.dataset.messageId === '104').length, 1, 'duplicate live message is idempotent');
  const retrySocket = sockets.at(-1);
  fetchQueue.push(response('RUNNING'));
  retrySocket.open(); await flush();
  fetchQueue.push(new Error('temporary failure'));
  retrySocket.receive({ type: 'task_complete', task_id: 'retry-task', thread_id: '7' });
  await flush();
  assert.strictEqual(manager.activeStreams.has('retry-task'), true, 'failed terminal sync must retain recoverable stream');
  fetchQueue.push(response('COMPLETED', [{ id: 103, actor: 'AGT', text: 'final' }]));
  await tick(10); await flush();
  assert.strictEqual(manager.activeStreams.has('retry-task'), false, 'later poll must recover terminal state');
  assert.strictEqual(messageNodes.filter((node) => node.dataset.messageId === '103').length, 1, 'final message is idempotent');
  assert.strictEqual(warnings.length, 1);
  assert.strictEqual(warnings[0][0], 'Unable to reconcile task state:');
  console.log('frontend_streaming_manager.test.js: ok');
})().catch((error) => { console.error(error); process.exitCode = 1; });
