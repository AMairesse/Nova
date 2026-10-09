/* Browser-free regression checks for HTML trust boundaries. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
    constructor() {
        this.dataset = {};
        this.style = {};
        this.innerHTML = '';
        this.textContent = '';
        this.children = new Map();
        this.classList = { add() {}, remove() {} };
    }
    setAttribute() {}
    querySelector(selector) {
        if (!this.children.has(selector)) this.children.set(selector, new Element());
        return this.children.get(selector);
    }
}
const appended = [];
const document = {
    createElement: () => new Element(),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    body: { appendChild: (element) => appended.push(element) },
};
const context = vm.createContext({ window: {}, document, gettext: (text) => text, setTimeout() {} });
for (const file of ['utils', 'message-renderer', 'streaming-manager']) {
    vm.runInContext(fs.readFileSync(`nova/static/js/${file}.js`, 'utf8'), context);
}
const attack = '"><img src=x onerror=alert(1)>';
const renderer = context.window.MessageRenderer;
const userMessage = renderer.createMessageElement({ id: 1, actor: 'USR', text: attack, file_count: attack }, 7);
assert.ok(!userMessage.innerHTML.includes(attack), 'user text and invalid file counters must not become HTML');
assert.ok(userMessage.innerHTML.includes('&lt;img'), 'user text must remain visible as text');
assert.ok(!userMessage.innerHTML.includes('file(s) attached'), 'invalid counters must be omitted');
assert.ok(renderer.createMessageElement({ id: 2, actor: 'USR', file_count: 3 }, 7).innerHTML.includes('3 file(s) attached'));

const manager = new context.window.StreamingManager();
manager.messageManager = { appendMessage: (element) => appended.push(element) };
manager.onUserPrompt('task', { interaction_id: attack, question: attack, origin_name: attack, schema: { enum: [attack] } });
assert.ok(!appended.at(-1).innerHTML.includes(attack), 'interaction identifiers, choices and text must be escaped');
manager.onSummarizationComplete({ original_tokens: attack, summary_tokens: attack, strategy: attack });
assert.ok(!appended.at(-1).innerHTML.includes(attack), 'summary metadata must never be parsed as HTML');
assert.ok(appended.at(-1).querySelector('small').textContent.includes(attack), 'summary metadata must remain plain text');
console.log('frontend_security_rendering.test.js: ok');
