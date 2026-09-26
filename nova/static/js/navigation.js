// Small navigation helpers shared by classic search deep links.
(function () {
    'use strict';

    function focusMessageFromURL() {
        const messageId = new URLSearchParams(window.location.search).get('message_id');
        if (!messageId) return;
        const focus = () => {
            const message = document.getElementById(`message-${messageId}`);
            if (!message) return false;
            message.scrollIntoView({ block: 'center' });
            message.classList.add('border', 'border-primary');
            window.setTimeout(() => message.classList.remove('border', 'border-primary'), 2400);
            return true;
        };
        if (!focus()) {
            let attempts = 0;
            const retry = () => {
                if (focus() || ++attempts >= 20) return;
                window.setTimeout(retry, 250);
            };
            window.setTimeout(retry, 250);
        }
    }

    async function loadOlderMessages(button) {
        const container = document.getElementById('conversation-container');
        const list = document.getElementById('messages-list');
        const threadId = document.querySelector('#message-container input[name="thread_id"]')?.value;
        if (!container || !list || !threadId || button.disabled) return;
        button.disabled = true;
        const height = container.scrollHeight;
        try {
            const params = new URLSearchParams({
                thread_id: threadId,
                offset: button.dataset.offset || '0',
                limit: container.dataset.messageLimit || '50',
            });
            const response = await fetch(`${window.NovaApp?.urls?.messageList}?${params}`);
            if (!response.ok) throw new Error('Unable to load older messages');
            const html = await response.text();
            const parsed = new DOMParser().parseFromString(html, 'text/html');
            const incoming = parsed.querySelector('#messages-list');
            if (!incoming) {
                button.disabled = false;
                return;
            }
            const empty = list.querySelector('[data-empty-state]');
            empty?.remove();
            Array.from(incoming.children).reverse().forEach((node) => list.prepend(node));
            const source = parsed.querySelector('#conversation-container');
            const nextOffset = source?.dataset.messagesNextOffset;
            const hasMore = source?.dataset.messagesHasMore === 'true';
            container.dataset.messagesNextOffset = nextOffset || '0';
            container.dataset.messagesHasMore = hasMore ? 'true' : 'false';
            if (hasMore) {
                button.dataset.offset = nextOffset;
                button.disabled = false;
            } else button.remove();
            container.scrollTop += container.scrollHeight - height;
        } catch (error) {
            console.error('Error loading older messages:', error);
            button.disabled = false;
        }
    }

    document.addEventListener('click', (event) => {
        const button = event.target.closest('#load-older-messages');
        if (button) {
            event.preventDefault();
            loadOlderMessages(button);
        }
    });
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', focusMessageFromURL, { once: true });
    } else {
        focusMessageFromURL();
    }
})();
