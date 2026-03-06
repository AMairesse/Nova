// static/nova/js/message-renderer.js
(function () {
    'use strict';

    // ============================================================================
    // MESSAGE RENDERER
    // ============================================================================
    window.MessageRenderer = class MessageRenderer {
        static renderUserText(text) {
            return window.DOMUtils.escapeHTML(text || '').replace(/\r\n|\r|\n/g, '<br>');
        }

        static getMessageAttachments(messageData) {
            if (Array.isArray(messageData?.message_attachments)) {
                return messageData.message_attachments;
            }
            if (Array.isArray(messageData?.internal_data?.message_attachments)) {
                return messageData.internal_data.message_attachments;
            }
            return [];
        }

        static createMessageElement(messageData, thread_id) {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message mb-3';
            messageDiv.id = `message-${messageData.id}`;
            messageDiv.setAttribute('data-message-id', messageData.id);
            const isContinuousPage = Boolean(window.NovaApp?.isContinuousPage);

            if (messageData.actor === 'SYS' || messageData.actor === 'system') {
                return this.createSystemMessageElement(messageData);
            } else if (messageData.actor === 'user' || messageData.actor === 'USR') {
                const attachments = this.getMessageAttachments(messageData);
                const textHtml = messageData.text
                    ? `<div class="user-message-text text-primary">${this.renderUserText(messageData.text)}</div>`
                    : '';
                const attachmentSummaryHtml = attachments.length
                    ? `
              <div class="${messageData.text ? 'mt-2 ' : ''}small text-muted">${attachments.length} image(s) attached</div>
              <div class="composer-attachment-summary">
                ${attachments.map((attachment) => `<span class="badge rounded-pill text-bg-light border me-1 mb-1">${window.DOMUtils.escapeHTML(attachment.filename || 'image')}</span>`).join('')}
              </div>
              `
                    : '';
                messageDiv.innerHTML = `
          <div class="card border-primary">
            <div class="card-body py-2">
              ${textHtml}
              ${attachmentSummaryHtml}
              ${messageData.file_count ? `<div class="mt-2 small text-muted">${messageData.file_count} file(s) attached</div>` : ''}
            </div>
          </div>
        `;
            } else if (messageData.actor === 'agent') {
                const compactLinkHtml = isContinuousPage ? '' : `
              <a href="#" class="compact-thread-link text-decoration-none small me-2 d-none" title="${gettext('Summarize conversation to save context space')}">
                <i class="bi bi-compress me-1"></i>${gettext('Compact')}
              </a>
              `;
                // Agent message structure
                messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content assistant-markdown">${window.DOMUtils.escapeHTML(messageData.text)}</div>
            </div>
            <div class="card-footer py-1 text-muted small d-flex justify-content-end align-items-center d-none">
              ${compactLinkHtml}
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
