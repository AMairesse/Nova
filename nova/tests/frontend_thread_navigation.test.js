/* Run with node nova/tests/frontend_thread_navigation.test.js. */
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
let links = [];
const panel = { innerHTML: '' };
const events = [];
const errors = [];
const document = {
  getElementById: () => panel,
  querySelector: () => ({ value: panel.innerHTML }),
  querySelectorAll: selector => selector.includes('threads-list') ? links : [],
  dispatchEvent: event => events.push(event),
};
const window = {
  NovaApp: { urls: { messageList: '/messages/' } },
  location: { href: 'https://nova.test/?thread_id=1&message_id=99#message-99' },
  history: { replaceState: (_state, _title, url) => { window.location.href = String(url); } },
};
const pending = [];
const context = { window, document, URL, gettext: x => x,
  CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  console: { error: (...args) => errors.push(args) },
  fetch: () => new Promise(resolve => pending.push(resolve)),
};
vm.createContext(context);
vm.runInContext(fs.readFileSync('nova/static/js/message-manager-threads.js', 'utf8'), context);
const methods = window.NovaApp.Modules.MessageThreadMethods;
const manager = { ...methods, currentThreadId: '1', streamingManager: { setInputAreaDisabled() {} } };
for (const name of ['saveComposerDraft', 'syncComposerAttachmentConfig', 'resetComposerAttachments',
  'resetComposerThreadFiles', 'restoreComposerDraft', 'initTextareaFocus', 'syncComposerTextStatus',
  'applyTemplateSetupPrefillFromUrl', 'updateVoiceButtonState', 'syncResponseModeControl',
  'syncComposerCapabilityNotice', 'primeBottomFollow', 'checkPendingInteractions',
  'updateCompactLinkVisibility', 'checkAndReconnectRunningTasks']) manager[name] = () => {};
const respond = (index, id) => pending[index]({ ok: true, status: 200, text: async () => id });
(async () => {
  let selections = [];
  const selectionManager = { currentThreadId: '1', loadMessages: async id => selections.push(id) };
  links = [{ dataset: { threadId: '2' } }];
  await methods.selectAfterThreadRemoval.call(selectionManager, '3');
  assert.deepStrictEqual(selections, [], 'removing another thread must preserve selection');
  await methods.selectAfterThreadRemoval.call(selectionManager, '1');
  assert.deepStrictEqual(selections, ['2'], 'removing the active thread selects a remaining thread');
  links = [];
  await methods.selectAfterThreadRemoval.call(selectionManager, '1');
  assert.deepStrictEqual(selections, ['2', null], 'removing the last thread requests an empty/default panel');

  const oldLoad = manager.loadMessages('1');
  const newLoad = manager.loadMessages('2');
  respond(1, '2');
  await newLoad;
  respond(0, '1');
  await oldLoad;
  assert.strictEqual(panel.innerHTML, '2', 'late response must not restore the removed/previous thread');
  assert.strictEqual(manager.currentThreadId, '2');
  assert.strictEqual(new URL(window.location.href).searchParams.get('message_id'), null);
  assert.strictEqual(events.length, 1, 'only the accepted panel emits threadChanged');

  const emptyLoad = manager.loadMessages('2');
  respond(2, '');
  await emptyLoad;
  assert.strictEqual(manager.currentThreadId, null, 'empty server panel must clear the selected id');
  assert.strictEqual(new URL(window.location.href).searchParams.get('thread_id'), null);
  assert.deepStrictEqual(errors, []);
  console.log('Thread navigation scenarios passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
