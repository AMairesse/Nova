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
                // Remove "compact" button from previous message footer
                const container = document.getElementById('messages-list');
                if (container) {
                    const selector = '.compact-thread-btn';
                    const buttons = container.querySelectorAll(selector);
                    const lastBtn = buttons[buttons.length - 1];
                    if (lastBtn) lastBtn.remove();
                }
                // Agent message structure
                messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content">${window.DOMUtils.escapeHTML(messageData.text)}</div>
            </div>
            <div class="card-footer py-1 text-muted small text-end d-none d-flex justify-content-end align-items-center">
              <div class="card-footer-consumption">
              </div>
              <button
                type="button"
                class="btn btn-link btn-sm text-decoration-none compact-thread-btn"
                data-thread-id="`+ thread_id + `"
              >
                <i class="bi bi-filter-circle me-1"></i>` + gettext('Compact') + `
              </button>
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
            if (messageData.internal_data && messageData.internal_data.type === 'compact_complete') {
                messageDiv.innerHTML = `
          <div class="card border-light">
            <div class="card-body py-2">
              <div class="text-muted small">
                ${window.DOMUtils.escapeHTML(messageData.text)}
                <button
                  class="btn btn-sm text-muted p-0 ms-1 border-0 bg-transparent"
                  type="button"
                  onclick="toggleCompactDetails(this)"
                  data-collapsed="true"
                  title="Show summary details"
                >
                  <small>[+ details]</small>
                </button>
              </div>
              <div class="compact-details mt-2 d-none">
                <div class="border-start border-secondary ps-2">
                  <small class="text-muted streaming-content">${window.DOMUtils.escapeHTML(messageData.internal_data.summary || '')}</small>
                </div>
              </div>
            </div>
          </div>
        `;
            } else {
                // Fallback for other system messages
                messageDiv.innerHTML = `
          <div class="card border-light">
            <div class="card-body py-2">
              <div class="text-muted small">${window.DOMUtils.escapeHTML(messageData.text)}</div>
            </div>
          </div>
        `;
            }

            return messageDiv;
        }
    };

    // Helper function to toggle compact details visibility
    function toggleCompactDetails(button) {
        const isCollapsed = button.dataset.collapsed === 'true';
        const messageDiv = button.closest('.message');
        const detailsDiv = messageDiv.querySelector('.compact-details');

        if (isCollapsed) {
            detailsDiv.classList.remove('d-none');
            button.querySelector('small').textContent = '[- details]';
            button.title = 'Hide summary details';
            button.dataset.collapsed = 'false';
        } else {
            detailsDiv.classList.add('d-none');
            button.querySelector('small').textContent = '[+ details]';
            button.title = 'Show summary details';
            button.dataset.collapsed = 'true';
        }
    }

    // Make function globally available for template onclick handlers
    window.toggleCompactDetails = toggleCompactDetails;

})();