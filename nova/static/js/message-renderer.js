// static/nova/js/message-renderer.js
(function () {
    'use strict';

    // ============================================================================
    // MESSAGE RENDERER
    // ============================================================================
    window.MessageRenderer = class MessageRenderer {
        static t(message) {
            return (window.gettext && typeof window.gettext === 'function') ? window.gettext(message) : message;
        }

        static renderUserText(text) {
            return window.DOMUtils.escapeHTML(text || '').replace(/\r\n|\r|\n/g, '<br>');
        }

        static getMessageAttachments(messageData) {
            if (Array.isArray(messageData?.artifacts)) {
                return messageData.artifacts;
            }
            if (Array.isArray(messageData?.message_artifacts)) {
                return messageData.message_artifacts;
            }
            if (Array.isArray(messageData?.message_attachments)) {
                return messageData.message_attachments;
            }
            if (Array.isArray(messageData?.internal_data?.message_attachments)) {
                return messageData.internal_data.message_attachments;
            }
            return [];
        }

        static isPublishableArtifact(attachment) {
            const metadata = attachment?.metadata || {};
            const hasContent = Boolean(attachment?.user_file_id || `${attachment?.summary_text || ''}`.trim());
            return Boolean(attachment?.id) && !attachment?.published_to_file && !metadata.legacy && hasContent;
        }

        static renderArtifactSummaryItem(attachment) {
            const label = window.DOMUtils.escapeHTML(attachment?.label || attachment?.filename || attachment?.kind || 'artifact');
            const kind = `${attachment?.kind || ''}`.trim();
            const kindSuffix = kind ? ` · ${window.DOMUtils.escapeHTML(kind)}` : '';
            const published = Boolean(attachment?.published_to_file);
            const publishedStateHtml = published
                ? `<span class="artifact-summary-state text-success small">${this.t('Added to Files')}</span>`
                : '';
            const publishButtonHtml = (!published && this.isPublishableArtifact(attachment))
                ? `
                <button
                    type="button"
                    class="btn btn-link btn-sm p-0 artifact-publish-btn"
                    data-artifact-id="${attachment.id}"
                    aria-label="${this.t('Add artifact to Files')}"
                >
                    ${this.t('Add to Files')}
                </button>
                `
                : '';

            return `
                <div class="artifact-summary-item" data-artifact-id="${attachment?.id || ''}" data-published-to-file="${published ? 'true' : 'false'}">
                    <span class="badge rounded-pill text-bg-light border me-1 mb-1">${label}${kindSuffix}</span>
                    ${publishedStateHtml || publishButtonHtml}
                </div>
            `;
        }

        static renderArtifactSummary(attachments, { withTopMargin = false } = {}) {
            if (!attachments.length) {
                return '';
            }
            return `
              <div class="${withTopMargin ? 'mt-3 ' : ''}composer-attachment-summary">
                ${attachments.map((attachment) => this.renderArtifactSummaryItem(attachment)).join('')}
              </div>
            `;
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
              <div class="${messageData.text ? 'mt-2 ' : ''}small text-muted">${attachments.length} artifact(s) attached</div>
              ${this.renderArtifactSummary(attachments)}
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
                const attachments = this.getMessageAttachments(messageData);
                const attachmentSummaryHtml = this.renderArtifactSummary(attachments, { withTopMargin: true });
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
              ${attachmentSummaryHtml}
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
