// static/nova/js/message-manager.js
(function () {
    'use strict';

    window.NovaApp = window.NovaApp || {};
    window.NovaApp.Modules = window.NovaApp.Modules || {};

    const MESSAGE_COMPOSER_METHOD_NAMES = [
        'handleFormSubmit',
        'triggerComposerSubmit',
        'resizeComposerTextarea',
        'syncComposerAttachmentConfig',
        'syncComposerTextStatus',
        'formatAttachmentSizeLabel',
        'interpolateMessage',
        'buildAttachmentCountLimitMessage',
        'buildAttachmentSizeLimitMessage',
        'openComposerAttachmentPicker',
        'handleComposerAttachmentInputChange',
        'handleComposerPaste',
        'handleComposerDragEnter',
        'handleComposerDragOver',
        'handleComposerDragLeave',
        'handleComposerDrop',
        'addComposerAttachments',
        'insertComposerText',
        'openComposerPasteDecisionModal',
        'buildComposerThreadFileName',
        'queueComposerThreadFile',
        'queueComposerThreadFileFromText',
        'processComposerTextInput',
        'processComposerDroppedFiles',
        'cloneComposerFile',
        'removeComposerAttachment',
        'resetComposerAttachments',
        'renderComposerAttachments',
        'removeComposerThreadFile',
        'resetComposerThreadFiles',
        'renderComposerThreadFiles',
        'buildComposerSubmissionMessage',
        'resolveComposerPasteDecision',
        'getComposerDropzoneElement',
        'resetComposerDropzoneState',
        'setComposerDropzoneActive',
        'eventHasComposerFiles',
        'isComposerTextFile',
        'getSelectedAgentCapabilityState',
        'getSelectedResponseMode',
        'updateResponseModeButton',
        'shouldShowResponseModeControl',
        'syncResponseModeControl',
        'getComposerBlockingCapabilityError',
        'syncComposerCapabilityNotice',
        'getComposerAttachmentKind',
        'getComposerAttachmentMaxBytes',
        'getComposerAttachmentTypeLabel',
    ];

    const MESSAGE_THREAD_METHOD_NAMES = [
        'loadMessages',
        'applyTemplateSetupPrefillFromUrl',
        'answerInteraction',
        'cancelInteraction',
        'createThread',
        'deleteThread',
        'summarizeCurrentThread',
        'showSubAgentConfirmationDialog',
        'confirmSummarize',
        'openDeleteTailPreview',
        'confirmDeleteTailAfter',
        'loadInitialThread',
        'getInitialThreadIdFromUrl',
        'checkPendingInteractions',
        'checkAndReconnectRunningTasks',
    ];

    const MESSAGE_DEVICE_METHOD_NAMES = [
        'initVoiceRecognition',
        'handleVoiceButtonClick',
        'handleVoiceResult',
        'handleVoiceError',
        'handleVoiceStart',
        'handleVoiceEnd',
        'updateVoiceButtonState',
        'showVoiceError',
        'initLongPressContextMenu',
        'handleTouchStart',
        'handleTouchEnd',
        'handleTouchMove',
        'handleTouchCancel',
        'cancelLongPress',
        'getMessageTraceTaskId',
        'getMessageContextSummary',
        'getMessageExecutionSummary',
        'showMessageContextMenu',
        'initContextMenuActions',
        'copyMessageToClipboard',
        'fallbackCopyToClipboard',
    ];

    function bindHelperMethods(target, methods, methodNames, helperName) {
        methodNames.forEach((name) => {
            const method = methods?.[name];
            if (typeof method !== 'function') {
                throw new Error(
                    `[MessageManager] Missing ${helperName}.${name}(). Check script load order.`
                );
            }
            target[name] = method.bind(target);
        });
    }

    // ============================================================================
    // MESSAGE MANAGER - Handles dynamic message insertion and scroll
    // ============================================================================
    window.MessageManager = class MessageManager {
        constructor() {
            this.voiceRecognition = null;
            this.currentThreadId = null;

            // Long press context menu state
            this.longPressTimer = null;
            this.longPressDuration = 500; // ms
            this.longPressTarget = null;
            this.touchStartPos = null;
            this.contextMenuOffcanvas = null;

            // Idempotence
            this._initialized = false;
            this._handlersBound = false;
            this._setupPrefillApplied = false;
            this.composerAttachments = [];
            this.composerThreadFiles = [];
            this.maxComposerAttachments = 4;
            this.maxComposerImageBytes = 4 * 1024 * 1024;
            this.maxComposerDocumentBytes = 10 * 1024 * 1024;
            this.maxComposerAudioBytes = 10 * 1024 * 1024;
            this.composerAttachmentSizeLabel = '4 MB';
            this.maxComposerSoftTextLimit = 8_000;
            this.maxComposerHardTextLimit = 12_000;
            this.maxComposerDroppedTextReadBytes = 1024 * 1024;
            this.isComposerSubmitting = false;
            this.pendingComposerPasteDecision = null;
            this.composerDragDepth = 0;
            this.executionTraceTaskId = '';
            this.executionTraceRefreshTimer = null;

            this.streamingManager = new window.StreamingManager();

            bindHelperMethods(
                this,
                window.NovaApp.Modules.MessageComposerMethods,
                MESSAGE_COMPOSER_METHOD_NAMES,
                'MessageComposerMethods'
            );
            bindHelperMethods(
                this,
                window.NovaApp.Modules.MessageThreadMethods,
                MESSAGE_THREAD_METHOD_NAMES,
                'MessageThreadMethods'
            );
            bindHelperMethods(
                this,
                window.NovaApp.Modules.MessageDeviceMethods,
                MESSAGE_DEVICE_METHOD_NAMES,
                'MessageDeviceMethods'
            );
            this.streamingManager.setMessageManager(this);
            this.initVoiceRecognition();
        }

        init() {
            if (this._initialized) return;
            this._initialized = true;

            // Attach event handlers
            this.attachEventHandlers();
            this.loadInitialThread();

            // Handle server-rendered interaction cards and check for pending interactions
            this.checkPendingInteractions();

            // Initialize long press context menu (mobile only)
            this.initLongPressContextMenu();
        }

        attachEventHandlers() {
            if (this._handlersBound) return;
            this._handlersBound = true;

            // 'click' event mapping
            const eventMappings = {
                '.thread-link': (e, target) => {
                    e.preventDefault();
                    const link = target.closest('.thread-link');
                    const threadId = link.dataset.threadId;
                    this.loadMessages(threadId);

                    // Mobile: close threads offcanvas after selection
                    if (window.innerWidth < 992) {
                        const ocEl = document.getElementById('threadsOffcanvas');
                        if (ocEl && window.bootstrap && bootstrap.Offcanvas) {
                            bootstrap.Offcanvas.getOrCreateInstance(ocEl).hide();
                        }
                    }
                },
                '.create-thread-btn': (e, target) => {
                    e.preventDefault();
                    this.createThread();
                },
                '.delete-thread-btn': (e, target) => {
                    e.preventDefault();
                    const btn = target.closest('.delete-thread-btn');
                    const threadId = btn.dataset.threadId;
                    this.deleteThread(threadId);
                },
                '.agent-dropdown-item': (e, target) => {
                    e.preventDefault();
                    const item = target.closest('.agent-dropdown-item');
                    const value = item.dataset.value;
                    const label = item.textContent.trim();
                    const selectedAgentInput = document.getElementById('selectedAgentInput');
                    const dropdownButton = document.getElementById('dropdownMenuButton');
                    if (selectedAgentInput) selectedAgentInput.value = value;
                    if (dropdownButton) {
                        dropdownButton.innerHTML = '<i class="bi bi-robot"></i>';
                        dropdownButton.setAttribute('title', label);
                    }
                    const responseModeInput = document.getElementById('responseModeInput');
                    if (responseModeInput) {
                        responseModeInput.value = 'auto';
                    }
                    document.querySelectorAll('.response-mode-item').forEach((entry) => {
                        entry.classList.toggle('active', entry.dataset.value === 'auto');
                    });
                    this.syncResponseModeControl();
                    this.syncComposerCapabilityNotice();
                },
                '.response-mode-item': (e, target) => {
                    e.preventDefault();
                    const item = target.closest('.response-mode-item');
                    const value = `${item?.dataset?.value || 'auto'}`.trim() || 'auto';
                    const input = document.getElementById('responseModeInput');
                    if (input) input.value = value;
                    document.querySelectorAll('.response-mode-item').forEach((entry) => {
                        entry.classList.toggle('active', entry === item);
                    });
                    this.syncResponseModeControl();
                    this.syncComposerCapabilityNotice();
                },
                '.interaction-answer-btn': (e, target) => {
                    e.preventDefault();
                    const btn = target.closest(".interaction-answer-btn");
                    const interactionId = btn.dataset.interactionId;
                    let answer;
                    if (Object.prototype.hasOwnProperty.call(btn.dataset, 'answerJson')) {
                        try {
                            answer = JSON.parse(btn.dataset.answerJson);
                        } catch (_error) {
                            answer = btn.dataset.answerJson;
                        }
                    } else {
                        const textarea = document.getElementById(`interaction-answer-input-${interactionId}`);
                        answer = textarea ? textarea.value : '';
                    }
                    this.answerInteraction(interactionId, answer);
                },
                '.interaction-cancel-btn': (e, target) => {
                    e.preventDefault();
                    const btn = target.closest(".interaction-cancel-btn");
                    const interactionId = btn.dataset.interactionId;
                    this.cancelInteraction(interactionId);
                },
                '#voice-btn': (e, target) => {
                    e.preventDefault();
                    this.handleVoiceButtonClick();
                },
                '#attach-image-btn': (e) => {
                    e.preventDefault();
                    this.openComposerAttachmentPicker('message-attachment-input');
                },
                '#camera-capture-btn': (e) => {
                    e.preventDefault();
                    this.openComposerAttachmentPicker('message-camera-input');
                },
                '.composer-mobile-action': (e, target) => {
                    e.preventDefault();
                    const action = `${target.closest('.composer-mobile-action')?.dataset?.action || ''}`.trim();
                    if (action === 'attach') {
                        this.openComposerAttachmentPicker('message-attachment-input');
                        return;
                    }
                    if (action === 'camera') {
                        this.openComposerAttachmentPicker('message-camera-input');
                        return;
                    }
                    if (action === 'voice') {
                        this.handleVoiceButtonClick();
                    }
                },
                '#send-btn': (e, target) => {
                    e.preventDefault();
                    const form = target.closest('form');
                    if (form) this.triggerComposerSubmit(form);
                },
                '.composer-attachment-remove': (e, target) => {
                    e.preventDefault();
                    const button = target.closest('.composer-attachment-remove');
                    this.removeComposerAttachment(button?.dataset.attachmentId || '');
                },
                '.composer-thread-file-remove': (e, target) => {
                    e.preventDefault();
                    const button = target.closest('.composer-thread-file-remove');
                    this.removeComposerThreadFile(button?.dataset?.threadFileId || '');
                },
                '#composer-paste-decision-close': (e) => {
                    e.preventDefault();
                    this.resolveComposerPasteDecision('cancel');
                },
                '#composer-paste-decision-cancel': (e) => {
                    e.preventDefault();
                    this.resolveComposerPasteDecision('cancel');
                },
                '#composer-paste-decision-keep': (e) => {
                    e.preventDefault();
                    this.resolveComposerPasteDecision('keep');
                },
                '#composer-paste-decision-file': (e) => {
                    e.preventDefault();
                    this.resolveComposerPasteDecision('file');
                },
                '.compact-thread-link': (e, target) => {
                    e.preventDefault();
                    this.summarizeCurrentThread();
                },
                '.execution-trace-link': (e, target) => {
                    e.preventDefault();
                    const link = target.closest('.execution-trace-link');
                    if (link) {
                        void this.openExecutionTrace(link);
                    }
                },
                '.delete-tail-link': (e, target) => {
                    e.preventDefault();
                    const link = target.closest('.delete-tail-link');
                    const messageId = `${link?.dataset?.messageId || link?.closest('.message')?.dataset?.messageId || ''}`.trim();
                    if (messageId) {
                        void this.openDeleteTailPreview(messageId);
                    }
                },
                '.task-progress-trace-link': (e, target) => {
                    e.preventDefault();
                    const button = target.closest('.task-progress-trace-link');
                    if (button) {
                        void this.openExecutionTrace(button);
                    }
                },
                '.message-context-menu-trigger': (e, target) => {
                    e.preventDefault();
                    const button = target.closest('.message-context-menu-trigger');
                    const messageCard = button?.closest('.card');
                    if (messageCard) {
                        this.showMessageContextMenu(messageCard);
                    }
                },
                '#delete-message-tail-confirm-btn': (e) => {
                    e.preventDefault();
                    void this.confirmDeleteTailAfter();
                }
            };

            // Generic handler for all 'click' events
            document.addEventListener('click', (e) => {
                for (const [selector, handler] of Object.entries(eventMappings)) {
                    if (e.target.matches(selector) || e.target.closest(selector)) {
                        handler(e, e.target.closest(selector) || e.target);
                        return;
                    }
                }
            });

            // Handle the textarea dynamic resizing
            // Using a delegation approach because the textarea is dynamically added
            document.addEventListener('input', (e) => {
                if (e.target.matches('#message-container textarea.auto-resize-textarea[name="new_message"]')) {
                    this.resizeComposerTextarea(e.target);
                    this.syncComposerTextStatus(e.target);
                }
            });

            // Form submission
            document.addEventListener('submit', async (e) => {
                if (e.target.id === 'message-form') {
                    e.preventDefault();
                    await this.triggerComposerSubmit(e.target);
                }
            });

            document.addEventListener('change', (e) => {
                if (e.target.id === 'message-attachment-input' || e.target.id === 'message-camera-input') {
                    void this.handleComposerAttachmentInputChange(e.target);
                }
            });

            document.addEventListener('paste', (e) => {
                if (e.target.matches('#message-container textarea[name="new_message"]')) {
                    void this.handleComposerPaste(e);
                }
            });

            document.addEventListener('dragenter', (e) => {
                if (this.eventHasComposerFiles(e)) {
                    this.handleComposerDragEnter(e);
                }
            });

            document.addEventListener('dragover', (e) => {
                if (this.eventHasComposerFiles(e)) {
                    this.handleComposerDragOver(e);
                }
            });

            document.addEventListener('dragleave', (e) => {
                if (this.eventHasComposerFiles(e)) {
                    this.handleComposerDragLeave(e);
                }
            });

            document.addEventListener('drop', (e) => {
                if (this.eventHasComposerFiles(e)) {
                    void this.handleComposerDrop(e);
                }
            });

            // Textarea handling
            document.addEventListener('keydown', (e) => {
                if (e.target.matches('#message-container textarea[name="new_message"]') && e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    const form = e.target.closest('form') || document.getElementById('message-form');
                    if (form) this.triggerComposerSubmit(form);
                }
            });

            const traceModal = document.getElementById('execution-trace-modal');
            if (traceModal && !traceModal._novaTraceBound) {
                traceModal._novaTraceBound = true;
                traceModal.addEventListener('hidden.bs.modal', () => {
                    this.executionTraceTaskId = '';
                    if (this.executionTraceRefreshTimer) {
                        window.clearTimeout(this.executionTraceRefreshTimer);
                        this.executionTraceRefreshTimer = null;
                    }
                });
            }
        }

        appendMessage(messageElement) {
            const messagesList = document.getElementById('messages-list');
            if (messagesList) {
                const emptyState = messagesList.querySelector('#messages-empty-state,[data-empty-state="true"]');
                if (emptyState) {
                    emptyState.remove();
                }
                messagesList.appendChild(messageElement);
            } else {
                console.error('Messages list not found!');
            }

            // Update compact link visibility after adding new message
            this.updateCompactLinkVisibility();

            // Auto-scroll to bottom when new messages are added
            this.scrollToBottom();
        }

        scrollToMessage(messageId) {
            const messageEl = document.getElementById(`message-${messageId}`);
            const container = document.getElementById('conversation-container');

            if (!messageEl || !container) return;

            // Calculate position to show message at upper part of screen
            const inputArea = document.querySelector('.message-input-area');
            const inputHeight = inputArea ? inputArea.offsetHeight : 0;
            const containerRect = container.getBoundingClientRect();
            const messageRect = messageEl.getBoundingClientRect();

            // Position message at 20% from top for better UX
            const targetTop = messageEl.offsetTop - (containerRect.height * 0.2);

            container.scrollTo({
                top: Math.max(0, targetTop),
                behavior: 'smooth'
            });
        }

        initTextareaFocus() {
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (textarea) textarea.focus();
        }

        scrollToBottom() {
            const container = document.getElementById('conversation-container');
            if (container) {
                // Use setTimeout to ensure DOM is updated before scrolling
                setTimeout(() => {
                    container.scrollTo({
                        top: container.scrollHeight,
                        behavior: 'smooth'
                    });
                }, 100);
            }
        }

        // Update compact link visibility based on message count and position
        updateCompactLinkVisibility() {
            const messagesList = document.getElementById('messages-list');
            if (!messagesList) return;
            const isContinuousPage = Boolean(window.NovaApp?.isContinuousPage);

            // Get all stored messages and agent messages
            const storedMessages = Array.from(messagesList.querySelectorAll('.message[data-message-id]'));
            const allMessages = messagesList.querySelectorAll('.message');
            const agentMessages = Array.from(messagesList.querySelectorAll('.message[data-message-actor="agent"]'));
            const lastStoredMessage = storedMessages.length ? storedMessages[storedMessages.length - 1] : null;

            // Reset footer controls on all agent messages first
            agentMessages.forEach(messageEl => {
                const compactLink = messageEl.querySelector('.compact-thread-link');
                const deleteTailLink = messageEl.querySelector('.delete-tail-link');
                const footer = messageEl.querySelector('.agent-message-footer');
                const hasContext = Boolean(this.getMessageContextSummary(messageEl));
                const hasTrace = Boolean(this.getMessageTraceTaskId(messageEl));
                const hasLaterMessage = Boolean(lastStoredMessage && lastStoredMessage !== messageEl);
                messageEl.dataset.canCompact = 'false';
                if (compactLink) {
                    compactLink.classList.add('d-none');
                }
                if (deleteTailLink) {
                    deleteTailLink.classList.toggle('d-none', !hasLaterMessage);
                }
                if (footer) {
                    footer.classList.toggle('d-none', !hasContext && !hasTrace && !hasLaterMessage);
                }
            });

            // Show compact link only on the last agent message if there are enough messages for compaction
            // (more messages than preserve_recent setting - we assume default of 2 for client-side)
            if (!isContinuousPage && allMessages.length > 2 && agentMessages.length > 0) {
                const lastAgentMessage = agentMessages[agentMessages.length - 1];
                const compactLink = lastAgentMessage.querySelector('.compact-thread-link');
                const footer = lastAgentMessage.querySelector('.agent-message-footer');
                if (compactLink) {
                    compactLink.classList.remove('d-none');
                }
                lastAgentMessage.dataset.canCompact = 'true';
                if (footer) {
                    footer.classList.remove('d-none');
                }
            }
        }

        buildExecutionTraceUrl(taskId) {
            const template = window.NovaApp?.urls?.taskExecutionTrace || '';
            if (!template || !taskId) {
                return '';
            }
            return template.replace('/0/', `/${encodeURIComponent(String(taskId))}/`);
        }

        formatExecutionDuration(durationMs) {
            const duration = Number(durationMs || 0);
            if (!duration) {
                return '';
            }
            if (duration < 1000) {
                return `${duration} ms`;
            }
            const seconds = duration / 1000;
            if (seconds < 60) {
                return `${seconds.toFixed(seconds >= 10 ? 0 : 1)} s`;
            }
            const minutes = Math.floor(seconds / 60);
            const remainingSeconds = Math.round(seconds % 60);
            return `${minutes} min ${remainingSeconds}s`;
        }

        getExecutionNodeTypeLabel(nodeType) {
            const mapping = {
                agent_run: gettext('Agent'),
                model_call: gettext('Model'),
                tool: gettext('Tool'),
                subagent: gettext('Sub-agent'),
                interaction: gettext('Interaction'),
                error: gettext('Error'),
            };
            return mapping[nodeType] || gettext('Step');
        }

        getExecutionStatusBadge(status) {
            const normalized = `${status || ''}`.trim().toLowerCase();
            const mapping = {
                completed: { label: gettext('Completed'), className: 'text-bg-success' },
                failed: { label: gettext('Failed'), className: 'text-bg-danger' },
                canceled: { label: gettext('Canceled'), className: 'text-bg-secondary' },
                awaiting_input: { label: gettext('Awaiting input'), className: 'text-bg-warning' },
                running: { label: gettext('Running'), className: 'text-bg-primary' },
            };
            return mapping[normalized] || { label: normalized || gettext('Unknown'), className: 'text-bg-secondary' };
        }

        buildExecutionProviderLabel(meta) {
            const data = (meta && typeof meta === 'object') ? meta : {};
            const provider = `${data.provider || ''}`.trim();
            const model = `${data.model || ''}`.trim();
            if (provider && model) {
                return `${provider} / ${model}`;
            }
            return provider || model;
        }

        buildExecutionSummaryLine(summary) {
            const data = (summary && typeof summary === 'object') ? summary : {};
            const parts = [];
            const traceSummaryText = window.MessageRenderer.buildExecutionSummary(data);
            if (traceSummaryText) {
                parts.push(traceSummaryText);
            }
            if (Number(data.error_count || 0) > 0) {
                parts.push(`${data.error_count} ${gettext(Number(data.error_count) === 1 ? 'error' : 'errors')}`);
            }
            const durationLabel = this.formatExecutionDuration(data.duration_ms);
            if (durationLabel) {
                parts.push(durationLabel);
            }
            const context = (data.context && typeof data.context === 'object') ? data.context : {};
            if (context.max_context && (context.real_tokens !== null && context.real_tokens !== undefined || context.approx_tokens)) {
                const consumed = context.real_tokens !== null && context.real_tokens !== undefined
                    ? context.real_tokens
                    : context.approx_tokens;
                const mode = context.real_tokens !== null && context.real_tokens !== undefined
                    ? gettext('real')
                    : gettext('approximated');
                parts.push(`${gettext('Context')}: ${consumed} / ${context.max_context} (${mode})`);
            }
            const providerLabel = this.buildExecutionProviderLabel(data);
            if (providerLabel) {
                parts.push(providerLabel);
            }
            return parts.join(' • ');
        }

        renderExecutionPreviewSection(label, value) {
            const text = `${value || ''}`.trim();
            if (!text) {
                return '';
            }
            return `
                <div class="execution-node-section">
                    <div class="execution-node-section-label">${window.DOMUtils.escapeHTML(label)}</div>
                    <pre class="execution-node-preview mb-0"><code>${window.DOMUtils.escapeHTML(text)}</code></pre>
                </div>
            `;
        }

        buildExecutionNodeFacts(node) {
            const facts = [];
            const meta = (node?.meta && typeof node.meta === 'object') ? node.meta : {};
            const providerLabel = this.buildExecutionProviderLabel(meta);

            if (node?.type === 'model_call') {
                if (providerLabel) facts.push(providerLabel);
                if (meta.response_mode && meta.response_mode !== 'text') {
                    facts.push(`${meta.response_mode}`);
                }
                const toolCallCount = Array.isArray(meta.tool_call_names) ? meta.tool_call_names.length : 0;
                if (toolCallCount > 0) {
                    facts.push(`${toolCallCount} ${gettext(toolCallCount === 1 ? 'tool call' : 'tool calls')}`);
                }
            } else if (node?.type === 'tool') {
                if (meta.kind === 'terminal' && meta.head_command) {
                    facts.push(`${meta.head_command}`);
                }
                if (meta.kind === 'delegate_to_agent' && meta.target_agent_name) {
                    facts.push(`${meta.target_agent_name}`);
                }
                const outputCount = Array.isArray(meta.output_paths) ? meta.output_paths.length : 0;
                const copiedOutputCount = Array.isArray(meta.output_paths_copied_back) ? meta.output_paths_copied_back.length : 0;
                if (outputCount > 0) {
                    facts.push(`${outputCount} ${gettext(outputCount === 1 ? 'file' : 'files')}`);
                } else if (copiedOutputCount > 0) {
                    facts.push(`${copiedOutputCount} ${gettext(copiedOutputCount === 1 ? 'file' : 'files')}`);
                }
                if (meta.error_kind) {
                    facts.push(`${meta.error_kind}`);
                }
            } else if (node?.type === 'subagent') {
                if (meta.agent_name) facts.push(`${meta.agent_name}`);
                if (meta.response_mode && meta.response_mode !== 'text') {
                    facts.push(`${meta.response_mode}`);
                }
                if (providerLabel) facts.push(providerLabel);
                const outputCount = Array.isArray(meta.output_paths) ? meta.output_paths.length : 0;
                if (outputCount > 0) {
                    facts.push(`${outputCount} ${gettext(outputCount === 1 ? 'file' : 'files')}`);
                }
            } else if (node?.type === 'interaction') {
                if (meta.schema_type) facts.push(`${meta.schema_type}`);
                if (meta.interaction_status) facts.push(`${meta.interaction_status.toLowerCase()}`);
            }

            return facts.slice(0, 3);
        }

        buildExecutionNodeSideLines(node) {
            const lines = [];
            const startedAt = node?.started_at ? new Date(node.started_at).toLocaleString() : '';
            if (startedAt) {
                lines.push(startedAt);
            }

            const facts = this.buildExecutionNodeFacts(node);
            if (!facts.length) {
                return lines;
            }

            if (node?.type === 'model_call') {
                const [providerLabel, ...otherFacts] = facts;
                if (providerLabel) {
                    lines.push(providerLabel);
                }
                if (otherFacts.length) {
                    lines.push(otherFacts.join(' • '));
                }
                return lines;
            }

            lines.push(facts.join(' • '));
            return lines;
        }

        renderExecutionFileLinks(files) {
            const items = Array.isArray(files) ? files : [];
            if (!items.length) {
                return '';
            }
            return `
                <div class="execution-node-section">
                    <div class="execution-node-section-label">${window.DOMUtils.escapeHTML(gettext('Files'))}</div>
                    <div class="execution-node-file-links">
                        ${items.map((fileRef) => `
                            <a
                              class="btn btn-sm btn-outline-secondary"
                              href="${window.DOMUtils.escapeHTML(String(fileRef.content_url || ''))}"
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              ${window.DOMUtils.escapeHTML(String(fileRef.label || fileRef.path || 'file'))}
                            </a>
                        `).join('')}
                    </div>
                </div>
            `;
        }

        renderExecutionTechnicalDetails(node) {
            const meta = (node?.meta && typeof node.meta === 'object') ? { ...node.meta } : {};
            delete meta.progress_message;
            delete meta.progress_end_message;
            if (!Object.keys(meta).length) {
                return '';
            }
            return `
                <details class="execution-node-technical">
                    <summary>${window.DOMUtils.escapeHTML(gettext('Technical details'))}</summary>
                    <pre class="execution-node-preview mb-0"><code>${window.DOMUtils.escapeHTML(JSON.stringify(meta, null, 2))}</code></pre>
                </details>
            `;
        }

        renderExecutionTraceNode(node, { isRoot = false } = {}) {
            if (!node || typeof node !== 'object') {
                return '';
            }
            const children = Array.isArray(node.children) ? node.children : [];
            const typeLabel = this.getExecutionNodeTypeLabel(node.type);
            const status = this.getExecutionStatusBadge(node.status);
            const durationLabel = this.formatExecutionDuration(node.duration_ms);
            const outputPreview = this.renderExecutionPreviewSection(gettext('Output'), node.output_preview);
            const inputPreview = this.renderExecutionPreviewSection(gettext('Input'), node.input_preview);
            const fileLinksHtml = this.renderExecutionFileLinks(node.resolved_files);
            const technicalHtml = this.renderExecutionTechnicalDetails(node);
            const sideLines = this.buildExecutionNodeSideLines(node);
            const sideHtml = sideLines.length
                ? `
                    <div class="execution-node-summary-side">
                        ${sideLines.map((line) => `<div class="execution-node-summary-side-line">${window.DOMUtils.escapeHTML(String(line))}</div>`).join('')}
                    </div>
                `
                : '';
            const shouldOpen = isRoot || ['failed', 'running', 'awaiting_input'].includes(`${node.status || ''}`.trim().toLowerCase());
            const contentHtml = `
                ${inputPreview}
                ${outputPreview}
                ${fileLinksHtml}
                ${technicalHtml}
                ${children.length ? `<div class="execution-node-children">${children.map((child) => this.renderExecutionTraceNode(child)).join('')}</div>` : ''}
            `;

            if (children.length || isRoot) {
                return `
                    <details class="execution-trace-node" data-node-id="${window.DOMUtils.escapeHTML(String(node.id || ''))}" ${shouldOpen ? 'open' : ''}>
                        <summary class="execution-trace-node-summary">
                            <div class="execution-node-summary-layout">
                                <div class="execution-node-summary-main">
                                    <div class="execution-node-label">${window.DOMUtils.escapeHTML(node.label || typeLabel)}</div>
                                    <div class="execution-node-title-row">
                                        <span class="execution-node-type text-muted">${window.DOMUtils.escapeHTML(typeLabel)}</span>
                                        <span class="badge ${status.className}">${window.DOMUtils.escapeHTML(status.label)}</span>
                                        ${durationLabel ? `<span class="execution-node-duration text-muted">${window.DOMUtils.escapeHTML(durationLabel)}</span>` : ''}
                                    </div>
                                </div>
                                ${sideHtml}
                            </div>
                        </summary>
                        <div class="execution-trace-node-body">
                            ${contentHtml}
                        </div>
                    </details>
                `;
            }

            return `
                <div class="execution-trace-node execution-trace-node-leaf" data-node-id="${window.DOMUtils.escapeHTML(String(node.id || ''))}">
                    <div class="execution-trace-node-summary">
                        <div class="execution-node-summary-layout">
                            <div class="execution-node-summary-main">
                                <div class="execution-node-label">${window.DOMUtils.escapeHTML(node.label || typeLabel)}</div>
                                <div class="execution-node-title-row">
                                    <span class="execution-node-type text-muted">${window.DOMUtils.escapeHTML(typeLabel)}</span>
                                    <span class="badge ${status.className}">${window.DOMUtils.escapeHTML(status.label)}</span>
                                    ${durationLabel ? `<span class="execution-node-duration text-muted">${window.DOMUtils.escapeHTML(durationLabel)}</span>` : ''}
                                </div>
                            </div>
                            ${sideHtml}
                        </div>
                    </div>
                    <div class="execution-trace-node-body">
                        ${contentHtml}
                    </div>
                </div>
            `;
        }

        collectExecutionIssues(node, issues = [], { isRoot = true, parentIssue = false } = {}) {
            if (!node || typeof node !== 'object') {
                return issues;
            }
            const status = `${node.status || ''}`.trim().toLowerCase();
            const isIssue = (
                status === 'failed' &&
                node.type !== 'error' &&
                !(isRoot && node.type === 'agent_run')
            );
            if (isIssue && !parentIssue) {
                issues.push(node);
            }
            const children = Array.isArray(node.children) ? node.children : [];
            children.forEach((child) => this.collectExecutionIssues(child, issues, {
                isRoot: false,
                parentIssue: parentIssue || isIssue,
            }));
            return issues;
        }

        renderExecutionOverview(summary) {
            const data = (summary && typeof summary === 'object') ? summary : {};
            const status = this.getExecutionStatusBadge(data.status);
            const overviewCards = [];
            const providerLabel = this.buildExecutionProviderLabel(data);
            const executionSummary = window.MessageRenderer.buildExecutionSummary(data);
            const durationLabel = this.formatExecutionDuration(data.duration_ms);
            const context = (data.context && typeof data.context === 'object') ? data.context : {};

            overviewCards.push({ label: gettext('Status'), value: status.label });
            if (providerLabel) overviewCards.push({ label: gettext('Model'), value: providerLabel });
            if (data.response_mode) overviewCards.push({ label: gettext('Mode'), value: String(data.response_mode) });
            if (executionSummary) overviewCards.push({ label: gettext('Activity'), value: executionSummary });
            if (durationLabel) overviewCards.push({ label: gettext('Duration'), value: durationLabel });
            if (context.max_context && (context.real_tokens !== null && context.real_tokens !== undefined || context.approx_tokens)) {
                const consumed = context.real_tokens !== null && context.real_tokens !== undefined
                    ? context.real_tokens
                    : context.approx_tokens;
                const mode = context.real_tokens !== null && context.real_tokens !== undefined
                    ? gettext('real')
                    : gettext('approximated');
                overviewCards.push({
                    label: gettext('Context'),
                    value: `${consumed} / ${context.max_context} (${mode})`,
                });
            }
            if (Number(data.files_created_count || 0) > 0) {
                overviewCards.push({
                    label: gettext('Files created'),
                    value: String(data.files_created_count),
                });
            }

            const filesHtml = this.renderExecutionFileLinks(data.resolved_output_files);
            return `
                <div class="execution-trace-section">
                    <div class="execution-trace-section-title">${window.DOMUtils.escapeHTML(gettext('Overview'))}</div>
                    <div class="execution-overview-grid">
                        ${overviewCards.map((card) => `
                            <div class="execution-overview-card">
                                <span class="execution-overview-label">${window.DOMUtils.escapeHTML(String(card.label))}</span>
                                <span class="execution-overview-value">${window.DOMUtils.escapeHTML(String(card.value))}</span>
                            </div>
                        `).join('')}
                    </div>
                    ${filesHtml}
                </div>
            `;
        }

        renderExecutionIssuesWarning(root) {
            const issueCount = this.collectExecutionIssues(root, []).length;
            if (!issueCount) {
                return '';
            }
            return `
                <div class="alert alert-warning execution-issues-warning" role="alert">
                    <strong>${window.DOMUtils.escapeHTML(String(issueCount))}</strong>
                    ${window.DOMUtils.escapeHTML(gettext(issueCount === 1 ? 'issue during processing.' : 'issues during processing.'))}
                    <span class="execution-issues-warning-detail">${window.DOMUtils.escapeHTML(gettext('See the timeline below for details.'))}</span>
                </div>
            `;
        }

        async loadExecutionTrace(taskId) {
            const url = this.buildExecutionTraceUrl(taskId);
            const modalEl = document.getElementById('execution-trace-modal');
            if (!url || !modalEl) {
                return;
            }

            const summaryEl = document.getElementById('execution-trace-modal-summary');
            const loadingEl = document.getElementById('execution-trace-modal-loading');
            const emptyEl = document.getElementById('execution-trace-modal-empty');
            const contentEl = document.getElementById('execution-trace-modal-content');
            const overviewEl = document.getElementById('execution-trace-modal-overview');
            const issuesEl = document.getElementById('execution-trace-modal-problems');
            const treeEl = document.getElementById('execution-trace-modal-tree');

            if (summaryEl) summaryEl.textContent = '';
            if (overviewEl) overviewEl.innerHTML = '';
            if (issuesEl) issuesEl.innerHTML = '';
            if (treeEl) treeEl.innerHTML = '';
            if (emptyEl) emptyEl.classList.add('d-none');
            if (contentEl) contentEl.classList.add('d-none');
            if (loadingEl) loadingEl.classList.remove('d-none');

            try {
                const response = await fetch(url, {
                    headers: { 'X-Requested-With': 'XMLHttpRequest' },
                });
                if (!response.ok) {
                    throw new Error(`trace_fetch_failed_${response.status}`);
                }
                const payload = await response.json();
                const trace = (payload && typeof payload.execution_trace === 'object') ? payload.execution_trace : {};
                const summary = (trace.summary && typeof trace.summary === 'object') ? trace.summary : {};
                const root = (trace.root && typeof trace.root === 'object') ? trace.root : null;

                if (summaryEl) {
                    summaryEl.textContent = this.buildExecutionSummaryLine(summary);
                }

                if (!root) {
                    if (emptyEl) emptyEl.classList.remove('d-none');
                    return;
                }

                if (overviewEl) {
                    overviewEl.innerHTML = this.renderExecutionOverview(summary);
                }
                if (issuesEl) {
                    issuesEl.innerHTML = this.renderExecutionIssuesWarning(root);
                }
                if (treeEl) {
                    treeEl.innerHTML = `
                        <div class="execution-trace-section">
                            <div class="execution-trace-section-title">${window.DOMUtils.escapeHTML(gettext('Timeline'))}</div>
                            ${this.renderExecutionTraceNode(root, { isRoot: true })}
                        </div>
                    `;
                }
                if (contentEl) {
                    contentEl.classList.remove('d-none');
                }
                this.executionTraceTaskId = String(taskId || '');
            } catch (error) {
                console.error('Error loading execution trace:', error);
                if (emptyEl) {
                    emptyEl.textContent = gettext('Could not load execution details for this message.');
                    emptyEl.classList.remove('d-none');
                }
            } finally {
                if (loadingEl) loadingEl.classList.add('d-none');
            }
        }

        scheduleExecutionTraceRefresh(taskId) {
            const normalized = `${taskId || ''}`.trim();
            const modalEl = document.getElementById('execution-trace-modal');
            if (!normalized || !this.executionTraceTaskId || normalized !== this.executionTraceTaskId) {
                return;
            }
            if (!modalEl || !modalEl.classList.contains('show')) {
                return;
            }
            if (this.executionTraceRefreshTimer) {
                window.clearTimeout(this.executionTraceRefreshTimer);
            }
            this.executionTraceRefreshTimer = window.setTimeout(() => {
                this.executionTraceRefreshTimer = null;
                void this.loadExecutionTrace(normalized);
            }, 250);
        }

        async openExecutionTrace(triggerOrTaskId) {
            const taskId = typeof triggerOrTaskId === 'string' || typeof triggerOrTaskId === 'number'
                ? String(triggerOrTaskId)
                : triggerOrTaskId?.dataset?.taskId;
            const url = this.buildExecutionTraceUrl(taskId);
            const modalEl = document.getElementById('execution-trace-modal');
            if (!url || !modalEl || !window.bootstrap?.Modal) {
                this.showToast(gettext('Execution trace is not available on this page.'), 'warning');
                return;
            }

            const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();
            await this.loadExecutionTrace(taskId);
        }

        showToast(message, type = 'info') {
            // Simple toast implementation - could be enhanced with a proper toast library
            const toast = document.createElement('div');
            toast.className = `alert alert-${type} alert-dismissible fade show position-fixed`;
            toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 300px;';
            toast.innerHTML = `
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;
            document.body.appendChild(toast);

            // Auto-remove after 5 seconds
            setTimeout(() => {
                if (toast.parentNode) {
                    toast.remove();
                }
            }, 5000);
        }

    };
})();
