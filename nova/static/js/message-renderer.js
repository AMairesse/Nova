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

        static getInternalData(messageData) {
            if (messageData && typeof messageData.internal_data === 'object' && messageData.internal_data !== null) {
                return messageData.internal_data;
            }
            return {};
        }

        static getMessageAttachments(messageData) {
            if (Array.isArray(messageData?.attachments)) {
                return messageData.attachments;
            }
            if (Array.isArray(messageData?.message_attachments)) {
                return messageData.message_attachments;
            }
            return [];
        }

        static renderArtifactSummaryItem(attachment) {
            const label = window.DOMUtils.escapeHTML(attachment?.label || attachment?.filename || attachment?.kind || 'attachment');
            const kind = `${attachment?.kind || ''}`.trim();
            const kindSuffix = kind ? ` · ${window.DOMUtils.escapeHTML(kind)}` : '';

            return `
                <div class="artifact-summary-item">
                    <span class="badge rounded-pill text-bg-light border me-1 mb-1">${label}${kindSuffix}</span>
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
            const label = window.DOMUtils.escapeHTML(attachment?.label || attachment?.filename || attachment?.kind || 'attachment');
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

        static renderMessageContextTrigger() {
            const label = window.DOMUtils.escapeHTML(this.t('Message options'));
            return `
              <button
                type="button"
                class="btn btn-link message-context-menu-trigger d-md-none"
                aria-label="${label}"
                title="${label}"
              >
                <i class="bi bi-three-dots"></i>
              </button>
            `;
        }

        static buildExecutionSummary(traceSummary) {
            const summary = (traceSummary && typeof traceSummary === 'object') ? traceSummary : {};
            const toolCalls = Number(summary.tool_calls || 0);
            const subagentCalls = Number(summary.subagent_calls || 0);
            const interactionCount = Number(summary.interaction_count || 0);
            const errorCount = Number(summary.error_count || 0);
            const parts = [];

            if (toolCalls > 0) {
                parts.push(`${toolCalls} ${this.t(toolCalls === 1 ? 'tool' : 'tools')}`);
            }
            if (subagentCalls > 0) {
                parts.push(`${subagentCalls} ${this.t(subagentCalls === 1 ? 'sub-agent' : 'sub-agents')}`);
            }
            if (parts.length === 0 && interactionCount > 0) {
                parts.push(`${interactionCount} ${this.t(interactionCount === 1 ? 'interaction' : 'interactions')}`);
            }
            if (parts.length === 0 && errorCount > 0) {
                parts.push(`${errorCount} ${this.t(errorCount === 1 ? 'error' : 'errors')}`);
            }
            return parts.slice(0, 2).join(' • ');
        }

        static buildContextSummary(internalData) {
            const data = internalData || {};
            const maxContext = data.max_context;
            if (maxContext === null || maxContext === undefined || `${maxContext}` === '') {
                return '';
            }
            if (data.real_tokens !== null && data.real_tokens !== undefined && `${data.real_tokens}` !== '') {
                return `${window.DOMUtils.escapeHTML(String(data.real_tokens))} / ${window.DOMUtils.escapeHTML(String(maxContext))} (${this.t('real')})`;
            }
            if (data.approx_tokens !== null && data.approx_tokens !== undefined && `${data.approx_tokens}` !== '') {
                return `${window.DOMUtils.escapeHTML(String(data.approx_tokens))} / ${window.DOMUtils.escapeHTML(String(maxContext))} (${this.t('approximated')})`;
            }
            if (data.context_tokens !== null && data.context_tokens !== undefined && `${data.context_tokens}` !== '') {
                return `${window.DOMUtils.escapeHTML(String(data.context_tokens))} / ${window.DOMUtils.escapeHTML(String(maxContext))} (${this.t('approximated')})`;
            }
            return '';
        }

        static renderContextConsumption(internalData) {
            const summary = this.buildContextSummary(internalData);
            return summary ? `${this.t('Context consumption')}: ${summary}` : '';
        }

        static renderContextFooterChipContent(internalData) {
            const summary = this.buildContextSummary(internalData);
            if (!summary) {
                return '';
            }
            return `
                <span class="agent-footer-chip-heading">${this.t('Context')}</span>
                <span class="agent-footer-chip-detail">${summary}</span>
            `;
        }

        static setAgentMessageMetadata(messageEl, messageData) {
            if (!messageEl) {
                return;
            }
            const internalData = this.getInternalData(messageData);
            const traceSummary = (internalData.trace_summary && typeof internalData.trace_summary === 'object')
                ? internalData.trace_summary
                : {};

            messageEl.dataset.messageActor = 'agent';
            messageEl.dataset.traceTaskId = internalData.trace_task_id ? String(internalData.trace_task_id) : '';
            messageEl.dataset.traceToolCalls = String(Number(traceSummary.tool_calls || 0));
            messageEl.dataset.traceSubagentCalls = String(Number(traceSummary.subagent_calls || 0));
            messageEl.dataset.traceInteractionCount = String(Number(traceSummary.interaction_count || 0));
            messageEl.dataset.traceErrorCount = String(Number(traceSummary.error_count || 0));
            messageEl.dataset.traceDurationMs = String(Number(traceSummary.duration_ms || 0));
            messageEl.dataset.contextRealTokens = internalData.real_tokens !== null && internalData.real_tokens !== undefined
                ? String(internalData.real_tokens)
                : '';
            messageEl.dataset.contextApproxTokens = internalData.approx_tokens !== null && internalData.approx_tokens !== undefined
                ? String(internalData.approx_tokens)
                : '';
            messageEl.dataset.contextFallbackTokens = internalData.context_tokens !== null && internalData.context_tokens !== undefined
                ? String(internalData.context_tokens)
                : '';
            messageEl.dataset.contextMaxContext = internalData.max_context !== null && internalData.max_context !== undefined
                ? String(internalData.max_context)
                : '';
            messageEl.dataset.isLastAgentMessage = messageData?.is_last_agent_message ? 'true' : 'false';
            messageEl.dataset.canCompact = 'false';
        }

        static renderAgentFooter(messageData, { isContinuousPage = false } = {}) {
            const internalData = this.getInternalData(messageData);
            const traceSummary = (internalData.trace_summary && typeof internalData.trace_summary === 'object')
                ? internalData.trace_summary
                : {};
            const hasTrace = Boolean(internalData.trace_task_id);
            const traceSummaryText = this.buildExecutionSummary(traceSummary);
            const contextSummaryText = this.buildContextSummary(internalData);
            const hasContext = Boolean(contextSummaryText);
            const contextChipContent = this.renderContextFooterChipContent(internalData);
            const compactLinkHtml = isContinuousPage ? '' : `
              <a href="#" class="agent-footer-chip agent-footer-chip-action compact-thread-link text-decoration-none d-none" title="${this.t('Summarize conversation to save context space')}" aria-label="${this.t('Summarize conversation to save context space')}">
                <span class="agent-footer-chip-heading">
                  <i class="bi bi-compress"></i>${this.t('Compact')}
                </span>
              </a>
            `;
            const executionHtml = hasTrace ? `
              <a
                href="#"
                class="agent-footer-chip agent-footer-chip-action execution-trace-link text-decoration-none"
                data-task-id="${window.DOMUtils.escapeHTML(String(internalData.trace_task_id))}"
                aria-label="${this.t('Inspect execution details')}"
              >
                <span class="agent-footer-chip-heading">
                  <i class="bi bi-list-check"></i>${this.t('Execution')}
                </span>
                ${traceSummaryText ? `<span class="agent-footer-chip-detail execution-trace-summary">${window.DOMUtils.escapeHTML(traceSummaryText)}</span>` : ''}
              </a>
            ` : '';
            const deleteTailHtml = `
              <a
                href="#"
                class="agent-footer-chip agent-footer-chip-action delete-tail-link text-decoration-none d-none"
                data-message-id="${window.DOMUtils.escapeHTML(String(messageData.id || ''))}"
                aria-label="${this.t('Delete messages after this')}"
              >
                <span class="agent-footer-chip-heading">
                  <i class="bi bi-trash3"></i>${this.t('Delete after')}
                </span>
              </a>
            `;
            const contextHtml = `
              <div class="agent-footer-chip agent-footer-chip-info card-footer-consumption${hasContext ? '' : ' d-none'}">
                ${contextChipContent}
              </div>
            `;
            const shouldHideFooter = !hasContext && !hasTrace;

            return `
              <div class="card-footer agent-message-footer py-1 text-muted small${shouldHideFooter ? ' d-none' : ''}">
                <div class="agent-footer-chip-row">
                  ${compactLinkHtml}
                  ${executionHtml}
                  ${deleteTailHtml}
                  ${contextHtml}
                </div>
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
                messageDiv.dataset.messageActor = 'user';
                const attachments = this.getMessageAttachments(messageData);
                const textHtml = messageData.text
                    ? `<div class="user-message-text text-primary">${this.renderUserText(messageData.text)}</div>`
                    : '';
                const inlineArtifactsHtml = this.renderInlineArtifacts(attachments, { withTopMargin: Boolean(messageData.text) });
                const attachmentSummaryHtml = attachments.length
                    ? `
              <div class="${messageData.text ? 'mt-2 ' : ''}small text-muted">${attachments.length} attachment(s) added</div>
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
                // Agent message structure
                messageDiv.innerHTML = `
          <div class="card border-secondary agent-message-card">
            ${this.renderMessageContextTrigger()}
            <div class="card-body py-2">
              <div class="streaming-content assistant-markdown">${contentHtml}</div>
              ${inlineArtifactsHtml}
              ${attachmentSummaryHtml}
            </div>
            ${this.renderAgentFooter(messageData, { isContinuousPage })}
          </div>
        `;
                this.setAgentMessageMetadata(messageDiv, messageData);
            }

            return messageDiv;
        }

        static createSystemMessageElement(messageData) {
            const messageDiv = document.createElement('div');
            messageDiv.className = 'message mb-3';
            messageDiv.id = `message-${messageData.id}`;
            messageDiv.setAttribute('data-message-id', messageData.id);
            messageDiv.dataset.messageActor = 'other';

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
