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
                this.scrollToBottom();
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
                        body: JSON.stringify({ answer: answer || '' })
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
                const threadElement = document.getElementById(`thread-item-${threadId}`);
                if (threadElement) threadElement.remove();

                const firstThread = document.querySelector('.thread-link');
                const firstThreadId = firstThread?.dataset.threadId;
                this.loadMessages(firstThreadId);
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
