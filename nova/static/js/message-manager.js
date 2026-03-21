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
        'addComposerAttachments',
        'insertComposerText',
        'openComposerPasteDecisionModal',
        'buildComposerThreadFileName',
        'queueComposerThreadFileFromText',
        'cloneComposerFile',
        'removeComposerAttachment',
        'resetComposerAttachments',
        'renderComposerAttachments',
        'removeComposerThreadFile',
        'resetComposerThreadFiles',
        'renderComposerThreadFiles',
        'buildComposerSubmissionMessage',
        'resolveComposerPasteDecision',
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
        'publishArtifact',
        'markArtifactAsPublished',
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
            this.isComposerSubmitting = false;
            this.pendingComposerPasteDecision = null;

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
                    // Get the answer from the textarea
                    const textarea = document.getElementById(`interaction-answer-input-${interactionId}`);
                    const answer = textarea ? textarea.value : '';
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
                '.artifact-publish-btn': (e, target) => {
                    e.preventDefault();
                    const button = target.closest('.artifact-publish-btn');
                    if (button) {
                        void this.publishArtifact(button);
                    }
                },
                '.compact-thread-link': (e, target) => {
                    e.preventDefault();
                    this.summarizeCurrentThread();
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

            // Textarea handling
            document.addEventListener('keydown', (e) => {
                if (e.target.matches('#message-container textarea[name="new_message"]') && e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    const form = e.target.closest('form') || document.getElementById('message-form');
                    if (form) this.triggerComposerSubmit(form);
                }
            });
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

            // Get all messages and agent messages
            const allMessages = messagesList.querySelectorAll('.message');
            const agentMessages = messagesList.querySelectorAll('.message .card.border-secondary');

            // Hide compact link on all agent messages first
            agentMessages.forEach(card => {
                const compactLink = card.querySelector('.compact-thread-link');
                if (compactLink) {
                    compactLink.classList.add('d-none');
                }
            });
            if (isContinuousPage) {
                return;
            }

            // Show compact link only on the last agent message if there are enough messages for compaction
            // (more messages than preserve_recent setting - we assume default of 2 for client-side)
            if (allMessages.length > 2 && agentMessages.length > 0) {  // Need more than preserve_recent messages
                const lastAgentCard = agentMessages[agentMessages.length - 1];
                const compactLink = lastAgentCard.querySelector('.compact-thread-link');
                if (compactLink) {
                    compactLink.classList.remove('d-none');
                }
            }
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
