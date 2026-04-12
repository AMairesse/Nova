(function () {
    'use strict';

    window.NovaApp = window.NovaApp || {};
    window.NovaApp.Modules = window.NovaApp.Modules || {};

    window.NovaApp.Modules.MessageThreadMethods = {
        async loadMessages(threadId) {
            try {
                const params = threadId ? `?thread_id=${threadId}` : '';
                const response = await fetch(
                    `${window.NovaApp.urls.messageList}${params}`,
                    { headers: { 'X-AJAX': 'true' } }
                );

                if (response.status === 404 && threadId) {
                    return this.loadMessages(null);
                }

                const html = await response.text();
                this.stopBottomFollow?.({ cancelPrimeTimers: true });
                document.getElementById('message-container').innerHTML = html;
                this.syncComposerAttachmentConfig();
                this.resetComposerAttachments();
                this.resetComposerThreadFiles();

                const renderedThreadId = document.querySelector(
                    '#message-container input[name="thread_id"]'
                )?.value;
                this.currentThreadId = renderedThreadId || threadId;

                document.querySelectorAll('.thread-link').forEach((link) => {
                    link.classList.remove('active');
                });
                document.querySelectorAll(
                    `.thread-link[data-thread-id="${this.currentThreadId}"]`
                ).forEach((link) => {
                    link.classList.add('active');
                });

                document.dispatchEvent(
                    new CustomEvent('threadChanged', {
                        detail: { threadId: this.currentThreadId || null }
                    })
                );

                this.initTextareaFocus();
                this.syncComposerTextStatus();
                this.applyTemplateSetupPrefillFromUrl();
                this.updateVoiceButtonState();
                this.syncResponseModeControl();
                this.syncComposerCapabilityNotice();
                this.primeBottomFollow({
                    force: true,
                    behavior: 'auto',
                    observeRoot: document.getElementById('conversation-container'),
                });
                this.checkPendingInteractions();
                this.updateCompactLinkVisibility();
                this.checkAndReconnectRunningTasks();

                const pendingCards = document.querySelectorAll('[data-interaction-id]');
                if (!pendingCards || pendingCards.length === 0) {
                    this.streamingManager.setInputAreaDisabled(false);
                }
            } catch (error) {
                console.error('Error loading messages:', error);
            }
        },

        applyTemplateSetupPrefillFromUrl() {
            if (this._setupPrefillApplied) return;

            const params = new URLSearchParams(window.location.search || '');
            const prefillMessage = (params.get('prefill_message') || '').trim();
            const agentId = (params.get('agent_id') || '').trim();

            if (!prefillMessage && !agentId) return;

            if (agentId) {
                const selectedAgentInput = document.getElementById('selectedAgentInput');
                const dropdownButton = document.getElementById('dropdownMenuButton');
                const selectedItem = document.querySelector(
                    `.agent-dropdown-item[data-value="${agentId}"]`
                );
                if (selectedAgentInput) selectedAgentInput.value = agentId;
                if (dropdownButton && selectedItem) {
                    dropdownButton.innerHTML = '<i class="bi bi-robot"></i>';
                    dropdownButton.setAttribute(
                        'title',
                        selectedItem.textContent.trim()
                    );
                }
            }

            if (prefillMessage) {
                const textarea = document.querySelector(
                    '#message-container textarea[name="new_message"]'
                );
                if (textarea) {
                    textarea.value = prefillMessage;
                    this.resizeComposerTextarea(textarea);
                    textarea.focus();
                }
            }

            params.delete('prefill_message');
            params.delete('agent_id');
            const qs = params.toString();
            const nextUrl = `${window.location.pathname}${qs ? `?${qs}` : ''}`;
            window.history.replaceState({}, '', nextUrl);

            this._setupPrefillApplied = true;
            this.syncComposerCapabilityNotice();
        },

        async answerInteraction(interactionId, answer) {
            const answerUrlTemplate = window.NovaApp?.urls?.interactionAnswer;
            if (!answerUrlTemplate) {
                console.error('Interaction answer URL is not configured');
                this.showToast(
                    gettext('Interaction action is not configured on this page.'),
                    'warning'
                );
                return;
            }
            const clickedBtn = document.querySelector(
                `.interaction-answer-btn[data-interaction-id="${interactionId}"]`
            );
            if (!clickedBtn || clickedBtn.disabled) return;
            const originalHtml = clickedBtn.innerHTML;
            clickedBtn.disabled = true;
            clickedBtn.innerHTML =
                '<i class="bi bi-hourglass-split"></i> ' + gettext('Processing…');
            try {
                const response = await window.DOMUtils.csrfFetch(
                    answerUrlTemplate.replace('0', interactionId),
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ answer: answer === undefined ? '' : answer })
                    }
                );
                if (!response.ok) throw new Error('Server error');
                await response.json();
                this.streamingManager.setInputAreaDisabled(false);
            } catch (error) {
                console.error('Error answering interaction:', error);
                clickedBtn.disabled = false;
                clickedBtn.innerHTML = originalHtml;
            }
        },

        async cancelInteraction(interactionId) {
            const cancelUrlTemplate = window.NovaApp?.urls?.interactionCancel;
            if (!cancelUrlTemplate) {
                console.error('Interaction cancel URL is not configured');
                this.showToast(
                    gettext('Interaction action is not configured on this page.'),
                    'warning'
                );
                return;
            }
            const clickedBtn = document.querySelector(
                `.interaction-cancel-btn[data-interaction-id="${interactionId}"]`
            );
            if (!clickedBtn || clickedBtn.disabled) return;
            const originalHtml = clickedBtn.innerHTML;
            clickedBtn.disabled = true;
            clickedBtn.innerHTML =
                '<i class="bi bi-hourglass-split"></i> ' + gettext('Processing…');
            try {
                const response = await window.DOMUtils.csrfFetch(
                    cancelUrlTemplate.replace('0', interactionId),
                    { method: 'POST' }
                );
                if (!response.ok) throw new Error('Server error');
                await response.json();
                this.streamingManager.setInputAreaDisabled(false);
            } catch (error) {
                console.error('Error canceling interaction:', error);
                clickedBtn.disabled = false;
                clickedBtn.innerHTML = originalHtml;
            }
        },

        async createThread() {
            try {
                const response = await window.DOMUtils.csrfFetch(
                    window.NovaApp.urls.createThread,
                    { method: 'POST' }
                );
                const data = await response.json();
                if (data.threadHtml) {
                    ['threads-list', 'mobile-threads-list'].forEach((containerId) => {
                        const container = document.getElementById(containerId);
                        if (!container) return;

                        const todayGroup = window.ThreadManager.UIUtils.ensureGroupContainer(
                            'today',
                            container
                        );
                        const ul = todayGroup
                            ? todayGroup.querySelector('ul.list-group')
                            : null;
                        if (ul) {
                            ul.insertAdjacentHTML('afterbegin', data.threadHtml);
                        }
                    });
                }

                await this.loadMessages(data.thread_id);

                document.querySelectorAll(
                    `.thread-link[data-thread-id="${data.thread_id}"]`
                ).forEach((link) => {
                    link.scrollIntoView({ block: 'nearest' });
                });

                if (window.innerWidth < 992) {
                    const offcanvasEl = document.getElementById('threadsOffcanvas');
                    if (offcanvasEl && window.bootstrap?.Offcanvas) {
                        window.bootstrap.Offcanvas.getOrCreateInstance(offcanvasEl).hide();
                    }
                }
            } catch (error) {
                console.error('Error creating thread:', error);
            }
        },

        async refreshThreadListsFromServer() {
            const baseUrl = window.NovaApp?.urls?.loadMoreThreads;
            if (!baseUrl) {
                return;
            }

            const limit = window.ThreadManager?.config?.pagination?.limit || 10;
            const response = await fetch(`${baseUrl}?offset=0&limit=${limit}`);
            if (!response.ok) {
                throw new Error(gettext('Failed to refresh thread list.'));
            }

            const data = await response.json();
            const html = `${data.html || ''}`;
            ['threads-list', 'mobile-threads-list'].forEach((containerId) => {
                const container = document.getElementById(containerId);
                if (container) {
                    container.innerHTML = html;
                }
            });

            [
                { containerId: 'load-more-container', buttonId: 'load-more-threads' },
                { containerId: 'mobile-load-more-container', buttonId: 'mobile-load-more-threads' }
            ].forEach(({ containerId, buttonId }) => {
                const container = document.getElementById(containerId);
                const button = document.getElementById(buttonId);

                if (!data.has_more) {
                    container?.remove();
                    return;
                }

                if (button) {
                    button.dataset.offset = `${data.next_offset || limit}`;
                    button.disabled = false;
                    const icon = button.querySelector('i');
                    if (icon) icon.className = 'bi bi-arrow-down-circle me-1';
                }
            });
        },

        async deleteThread(threadId) {
            try {
                const response = await window.DOMUtils.csrfFetch(
                    window.NovaApp.urls.deleteThread.replace('0', threadId),
                    { method: 'POST' }
                );
                const data = await response.json();
                if (!response.ok || data.status !== 'OK') {
                    throw new Error(data.message || gettext('Failed to delete thread.'));
                }
                const escapedThreadId = window.CSS?.escape
                    ? window.CSS.escape(`${threadId}`)
                    : `${threadId}`.replace(/"/g, '\\"');

                document.querySelectorAll(
                    `[data-thread-item-id="${escapedThreadId}"]`
                ).forEach((threadElement) => {
                    threadElement.remove();
                });

                try {
                    await this.refreshThreadListsFromServer();
                } catch (refreshError) {
                    console.warn('Failed to refresh thread lists after deletion:', refreshError);
                    window.ResponsiveManager?.syncThreadLists?.();
                }

                const firstThread = document.querySelector(
                    '#threads-list .thread-link'
                );
                const firstThreadId = firstThread?.dataset.threadId
                    || document.querySelector('#mobile-threads-list .thread-link')?.dataset.threadId;
                await this.loadMessages(firstThreadId || null);
                document.dispatchEvent(
                    new CustomEvent('threadChanged', {
                        detail: { threadId: firstThreadId || null }
                    })
                );
            } catch (error) {
                console.error('Error deleting thread:', error);
                this.showToast(error.message || gettext('Failed to delete thread.'), 'warning');
            }
        },

        async summarizeCurrentThread() {
            if (window.NovaApp?.isContinuousPage) {
                return;
            }
            if (!this.currentThreadId) {
                alert('No thread selected');
                return;
            }

            const compactLinks = document.querySelectorAll('.compact-thread-link');
            if (compactLinks.length === 0) return;

            const originalHtmls = Array.from(compactLinks).map((link) => link.innerHTML);

            compactLinks.forEach((link) => {
                link.innerHTML =
                    '<i class="bi bi-hourglass-split me-1"></i>' +
                    gettext('Starting...');
                link.style.pointerEvents = 'none';
                link.style.opacity = '0.6';
            });

            try {
                const response = await window.DOMUtils.csrfFetch(
                    window.NovaApp.urls.summarizeThread.replace('0', this.currentThreadId),
                    { method: 'POST' }
                );

                const data = await response.json();

                if (data.status === 'CONFIRMATION_NEEDED') {
                    this.showSubAgentConfirmationDialog(data.sub_agents, data.thread_id);

                    compactLinks.forEach((link, index) => {
                        link.innerHTML = originalHtmls[index];
                        link.style.pointerEvents = '';
                        link.style.opacity = '';
                    });
                } else if (data.status === 'OK' && data.task_id) {
                    this.streamingManager.registerStream(data.task_id, {
                        id: data.task_id,
                        actor: 'system',
                        text: ''
                    });

                    compactLinks.forEach((link) => {
                        link.innerHTML =
                            '<i class="bi bi-hourglass-split me-1"></i>' +
                            gettext('Running...');
                    });
                } else {
                    throw new Error(data.message || 'Summarization failed');
                }
            } catch (error) {
                console.error('Error summarizing thread:', error);

                compactLinks.forEach((link, index) => {
                    link.innerHTML = originalHtmls[index];
                    link.style.pointerEvents = '';
                    link.style.opacity = '';
                });

                alert('Failed to start summarization: ' + error.message);
            }
        },

        showSubAgentConfirmationDialog(subAgents, threadId) {
            const list = document.getElementById('subAgentList');
            if (list) {
                list.innerHTML = subAgents
                    .map(
                        (agent) =>
                            `<li class="list-group-item">${agent.name} (${agent.token_count} ${gettext('tokens')})</li>`
                    )
                    .join('');
            }

            const modal = document.getElementById('subAgentConfirmationModal');
            if (modal) {
                modal.dataset.threadId = threadId;
                modal.dataset.subAgents = JSON.stringify(subAgents);

                const bsModal = new bootstrap.Modal(modal);
                bsModal.show();
            }
        },

        async confirmSummarize(includeSubAgents) {
            const modal = document.getElementById('subAgentConfirmationModal');
            if (!modal) return;

            const threadId = modal.dataset.threadId;
            const subAgents = JSON.parse(modal.dataset.subAgents || '[]');
            const subAgentIds = includeSubAgents ? subAgents.map((a) => a.id) : [];

            const bsModal = bootstrap.Modal.getInstance(modal);
            if (bsModal) {
                bsModal.hide();
            }

            const compactLinks = document.querySelectorAll('.compact-thread-link');
            compactLinks.forEach((link) => {
                link.innerHTML =
                    '<i class="bi bi-hourglass-split me-1"></i>' +
                    gettext('Starting...');
                link.style.pointerEvents = 'none';
                link.style.opacity = '0.6';
            });

            try {
                const response = await window.DOMUtils.csrfFetch(
                    window.NovaApp.urls.confirmSummarizeThread.replace('0', threadId),
                    {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded'
                        },
                        body: new URLSearchParams({
                            include_sub_agents: includeSubAgents,
                            sub_agent_ids: JSON.stringify(subAgentIds)
                        })
                    }
                );

                const data = await response.json();

                if (data.status === 'OK' && data.task_id) {
                    this.streamingManager.registerStream(data.task_id, {
                        id: data.task_id,
                        actor: 'system',
                        text: ''
                    });

                    compactLinks.forEach((link) => {
                        link.innerHTML =
                            '<i class="bi bi-hourglass-split me-1"></i>' +
                            gettext('Running...');
                    });
                } else {
                    throw new Error(data.message || 'Summarization failed');
                }
            } catch (error) {
                console.error('Error confirming summarization:', error);

                compactLinks.forEach((link) => {
                    link.innerHTML =
                        '<i class="bi bi-compress me-1"></i>' + gettext('Compact');
                    link.style.pointerEvents = '';
                    link.style.opacity = '';
                });

                alert('Failed to start summarization: ' + error.message);
            }
        },

        async openDeleteTailPreview(messageId) {
            const normalizedMessageId = `${messageId || ''}`.trim();
            const modalEl = document.getElementById('delete-message-tail-modal');
            const previewUrlTemplate = window.NovaApp?.urls?.previewDeleteMessageTail;
            if (!normalizedMessageId || !modalEl || !previewUrlTemplate || !window.bootstrap?.Modal) {
                this.showToast(gettext('Delete preview is not available on this page.'), 'warning');
                return;
            }

            try {
                const response = await fetch(
                    previewUrlTemplate.replace('0', normalizedMessageId),
                    { headers: { 'X-AJAX': 'true' } }
                );
                const payload = await response.json();
                if (!response.ok || payload.status !== 'OK') {
                    throw new Error(payload.message || gettext('Failed to prepare the deletion preview.'));
                }

                if (!payload.message_count) {
                    this.showToast(gettext('There are no later messages to delete.'), 'info');
                    return;
                }

                modalEl.dataset.messageId = normalizedMessageId;

                const summaryEl = document.getElementById('delete-message-tail-modal-summary');
                if (summaryEl) {
                    summaryEl.textContent = gettext('%s later messages').replace('%s', String(payload.message_count));
                }

                const messageEl = document.getElementById('delete-message-tail-modal-message');
                if (messageEl) {
                    const messageText = gettext(
                        'This will permanently delete %s later messages and %s attributable files.'
                    )
                        .replace('%s', String(payload.message_count))
                        .replace('%s', String(payload.file_count || 0));
                    messageEl.textContent = messageText;
                }

                const untrackedEl = document.getElementById('delete-message-tail-modal-untracked');
                if (untrackedEl) {
                    untrackedEl.textContent = gettext(
                        'Some historical files may remain because their provenance cannot be proven safely.'
                    );
                    untrackedEl.classList.toggle('d-none', !payload.has_untracked_files);
                }

                const filesWrapperEl = document.getElementById('delete-message-tail-modal-files-wrapper');
                const filesListEl = document.getElementById('delete-message-tail-modal-files');
                if (filesWrapperEl && filesListEl) {
                    const files = Array.isArray(payload.files) ? payload.files : [];
                    filesListEl.innerHTML = files
                        .map((file) => {
                            const label = window.DOMUtils.escapeHTML(file.label || file.path || gettext('File'));
                            const path = file.path
                                ? `<div class="small text-muted text-break">${window.DOMUtils.escapeHTML(file.path)}</div>`
                                : '';
                            const meta = [];
                            if (file.mime_type) {
                                meta.push(window.DOMUtils.escapeHTML(file.mime_type));
                            }
                            if (Number.isFinite(Number(file.size)) && Number(file.size) > 0) {
                                meta.push(window.DOMUtils.escapeHTML(this.formatAttachmentSizeLabel(Number(file.size))));
                            }
                            const metaLine = meta.length
                                ? `<div class="small text-muted">${meta.join(' • ')}</div>`
                                : '';
                            return `
                                <div class="list-group-item">
                                    <div class="fw-semibold text-break">${label}</div>
                                    ${path}
                                    ${metaLine}
                                </div>
                            `;
                        })
                        .join('');
                    filesWrapperEl.classList.toggle('d-none', files.length === 0);
                }

                const confirmBtn = document.getElementById('delete-message-tail-confirm-btn');
                if (confirmBtn) {
                    confirmBtn.disabled = false;
                    confirmBtn.innerHTML = gettext('Delete messages');
                }

                window.bootstrap.Modal.getOrCreateInstance(modalEl).show();
            } catch (error) {
                console.error('Error preparing delete tail preview:', error);
                this.showToast(
                    error.message || gettext('Failed to prepare the deletion preview.'),
                    'warning'
                );
            }
        },

        async confirmDeleteTailAfter() {
            const modalEl = document.getElementById('delete-message-tail-modal');
            const confirmBtn = document.getElementById('delete-message-tail-confirm-btn');
            const deleteUrlTemplate = window.NovaApp?.urls?.deleteMessageTail;
            const messageId = `${modalEl?.dataset?.messageId || ''}`.trim();
            if (!modalEl || !confirmBtn || !deleteUrlTemplate || !messageId) {
                return;
            }

            const originalHtml = confirmBtn.innerHTML;
            confirmBtn.disabled = true;
            confirmBtn.innerHTML = gettext('Deleting…');

            try {
                const response = await window.DOMUtils.csrfFetch(
                    deleteUrlTemplate.replace('0', messageId),
                    { method: 'POST' }
                );
                const payload = await response.json();
                if (!response.ok || payload.status !== 'OK') {
                    throw new Error(payload.message || gettext('Failed to delete later messages.'));
                }

                const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
                modal.hide();

                document.dispatchEvent(
                    new CustomEvent('nova:sidebar-refresh-request', {
                        detail: { files: true }
                    })
                );

                if (window.NovaApp?.isContinuousPage) {
                    window.location.href = payload.redirect_url || window.location.href;
                    return;
                }

                await this.loadMessages(this.currentThreadId || null);
                this.showToast(gettext('Later messages deleted.'), 'success');
            } catch (error) {
                console.error('Error deleting later messages:', error);
                this.showToast(
                    error.message || gettext('Failed to delete later messages.'),
                    'warning'
                );
                confirmBtn.disabled = false;
                confirmBtn.innerHTML = originalHtml;
            }
        },

        loadInitialThread() {
            if (window.NovaApp?.isContinuousPage) {
                return;
            }
            const threadIdFromUrl = this.getInitialThreadIdFromUrl();
            this.loadMessages(threadIdFromUrl);
        },

        getInitialThreadIdFromUrl() {
            const params = new URLSearchParams(window.location.search || '');
            const threadId = (params.get('thread_id') || '').trim();
            if (!/^\d+$/.test(threadId)) {
                return null;
            }
            return threadId;
        },

        checkPendingInteractions() {
            const pendingCards = document.querySelectorAll('[data-interaction-id]');
            if (pendingCards.length > 0) {
                this.streamingManager.setInputAreaDisabled(true);
            }
        },

        async checkAndReconnectRunningTasks() {
            if (!this.currentThreadId) return;

            try {
                const response = await fetch(
                    `${window.NovaApp.urls.runningTasksBase}${this.currentThreadId}/`
                );
                const data = await response.json();

                if (data.running_tasks && data.running_tasks.length > 0) {
                    for (const task of data.running_tasks) {
                        this.streamingManager.reconnectToTask(
                            task.id,
                            task.current_response,
                            task.last_progress
                        );
                    }
                } else {
                    const progressDiv = document.getElementById('task-progress');
                    if (progressDiv) progressDiv.classList.add('d-none');
                    const pendingCards = document.querySelectorAll('[data-interaction-id]');
                    if (!pendingCards || pendingCards.length === 0) {
                        this.streamingManager.setInputAreaDisabled(false);
                    }
                }
            } catch (error) {
                console.error('Error checking running tasks:', error);
            }
        },
    };
})();
