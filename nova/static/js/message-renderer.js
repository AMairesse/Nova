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
            return Boolean(attachment?.id) && !metadata.legacy && hasContent;
        }

        static renderArtifactSummaryItem(attachment) {
            const label = window.DOMUtils.escapeHTML(attachment?.label || attachment?.filename || attachment?.kind || 'artifact');
            const kind = `${attachment?.kind || ''}`.trim();
            const kindSuffix = kind ? ` · ${window.DOMUtils.escapeHTML(kind)}` : '';
            const published = Boolean(attachment?.published_to_file);
            const publishButtonHtml = this.isPublishableArtifact(attachment)
                ? `
                <button
                    type="button"
                    class="btn btn-link btn-sm p-0 artifact-publish-btn${published ? ' artifact-publish-btn-published text-success' : ''}"
                    data-artifact-id="${attachment.id}"
                    aria-label="${this.t('Add artifact to Files')}"
                >
                    ${published ? this.t('Added to Files') : this.t('Add to Files')}
                </button>
                `
                : '';

            return `
                <div class="artifact-summary-item" data-artifact-id="${attachment?.id || ''}" data-published-to-file="${published ? 'true' : 'false'}">
                    <span class="badge rounded-pill text-bg-light border me-1 mb-1">${label}${kindSuffix}</span>
                    ${publishButtonHtml}
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

        static renderInlineArtifact(attachment) {
            const contentUrl = `${attachment?.content_url || attachment?.preview_url || ''}`.trim();
            const label = window.DOMUtils.escapeHTML(attachment?.label || attachment?.filename || attachment?.kind || 'artifact');
            const kind = `${attachment?.kind || ''}`.trim();
            if (!contentUrl) {
                return '';
            }

            if (kind === 'image') {
                return `
                    <div class="artifact-inline-card artifact-inline-card-image">
                        <img src="${window.DOMUtils.escapeHTML(contentUrl)}" alt="${label}" class="artifact-inline-image img-fluid rounded border">
                    </div>
                `;
            }

            if (kind === 'audio') {
                return `
                    <div class="artifact-inline-card artifact-inline-card-audio">
                        <div class="small fw-semibold mb-2">${label}</div>
                        <audio controls preload="metadata" class="w-100" src="${window.DOMUtils.escapeHTML(contentUrl)}"></audio>
                    </div>
                `;
            }

            if (kind === 'pdf') {
                return `
                    <div class="artifact-inline-card artifact-inline-card-pdf">
                        <div class="d-flex align-items-center justify-content-between gap-2">
                            <div class="d-flex align-items-center gap-2">
                                <i class="bi bi-file-earmark-pdf fs-4 text-danger"></i>
                                <div>
                                    <div class="fw-semibold">${label}</div>
                                    <div class="small text-muted">${this.t('PDF document')}</div>
                                </div>
                            </div>
                            <a href="${window.DOMUtils.escapeHTML(contentUrl)}" target="_blank" rel="noopener noreferrer" class="btn btn-sm btn-outline-secondary">
                                ${this.t('Open')}
                            </a>
                        </div>
                    </div>
                `;
            }

            return '';
        }

        static renderInlineArtifacts(attachments, { withTopMargin = false } = {}) {
            const items = attachments
                .map((attachment) => this.renderInlineArtifact(attachment))
                .filter(Boolean);
            if (!items.length) {
                return '';
            }
            return `
                <div class="${withTopMargin ? 'mt-3 ' : ''}artifact-inline-list">
                    ${items.join('')}
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
                const inlineArtifactsHtml = this.renderInlineArtifacts(attachments, { withTopMargin: Boolean(messageData.text) });
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
              ${inlineArtifactsHtml}
              ${attachmentSummaryHtml}
              ${messageData.file_count ? `<div class="mt-2 small text-muted">${messageData.file_count} file(s) attached</div>` : ''}
            </div>
          </div>
        `;
            } else if (messageData.actor === 'agent' || messageData.actor === 'AGT') {
                const attachments = this.getMessageAttachments(messageData);
                const inlineArtifactsHtml = this.renderInlineArtifacts(attachments, { withTopMargin: true });
                const attachmentSummaryHtml = this.renderArtifactSummary(attachments, { withTopMargin: true });
                const renderedHtml = `${messageData.rendered_html || ''}`.trim();
                const contentHtml = renderedHtml || window.DOMUtils.escapeHTML(messageData.text || '');
                const compactLinkHtml = isContinuousPage ? '' : `
              <a href="#" class="compact-thread-link text-decoration-none small me-2 d-none" title="${gettext('Summarize conversation to save context space')}">
                <i class="bi bi-compress me-1"></i>${gettext('Compact')}
              </a>
              `;
                // Agent message structure
                messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content assistant-markdown">${contentHtml}</div>
              ${inlineArtifactsHtml}
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
