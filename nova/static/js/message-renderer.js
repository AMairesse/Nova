// static/nova/js/message-renderer.js
(function () {
    'use strict';

    // ============================================================================
    // MESSAGE RENDERER
    // ============================================================================
    window.MessageRenderer = class MessageRenderer {
        static createMessageElement(messageData, thread_id) {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message mb-3';
            messageDiv.id = `message-${messageData.id}`;
            messageDiv.setAttribute('data-message-id', messageData.id);

            if (messageData.actor === 'SYS' || messageData.actor === 'system') {
                return this.createSystemMessageElement(messageData);
            } else if (messageData.actor === 'user' || messageData.actor === 'USR') {
                messageDiv.innerHTML = `
          <div class="card border-primary">
            <div class="card-body py-2">
              <strong class="text-primary">${window.DOMUtils.escapeHTML(messageData.text)}</strong>
              ${messageData.file_count ? `<div class="mt-2 small text-muted">${messageData.file_count} file(s) attached</div>` : ''}
            </div>
          </div>
        `;
            } else if (messageData.actor === 'agent') {
                // Agent message structure
                messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content">${window.DOMUtils.escapeHTML(messageData.text)}</div>
            </div>
            <div class="card-footer py-1 text-muted small text-end d-none d-flex justify-content-end align-items-center">
              <div class="card-footer-consumption">
              </div>
            </div>
          </div>
        `;
            }

            return messageDiv;
        }

        static createSystemMessageElement(messageData) {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message mb-3';
            messageDiv.id = `message-${messageData.id}`;
            messageDiv.setAttribute('data-message-id', messageData.id);

            // System message rendering
            messageDiv.innerHTML = `
      <div class="card border-light">
        <div class="card-body py-2">
          <div class="text-muted small">${window.DOMUtils.escapeHTML(messageData.text)}</div>
        </div>
      </div>
    `;

            return messageDiv;
        }
    };


})();