// static/nova/js/message-manager.js
(function () {
    'use strict';

    // ============================================================================
    // MESSAGE MANAGER - Handles dynamic message insertion and scroll
    // ============================================================================
    window.MessageManager = class MessageManager {
        constructor() {
            this.streamingManager = new window.StreamingManager();
            this.streamingManager.setMessageManager(this);
            this.currentThreadId = null;
            this.voiceRecognition = null;
            this.initVoiceRecognition();

            // Long press context menu state
            this.longPressTimer = null;
            this.longPressDuration = 500; // ms
            this.longPressTarget = null;
            this.touchStartPos = null;
            this.contextMenuOffcanvas = null;

            // Idempotence
            this._initialized = false;
            this._handlersBound = false;
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
                    e.target.style.height = 'auto'; // Reset to auto for accurate scrollHeight
                    e.target.style.height = `${Math.min(e.target.scrollHeight, 200)}px`; // Adjust to content, cap at 200px max
                }
            });

            // Form submission
            document.addEventListener('submit', async (e) => {
                if (e.target.id === 'message-form') {
                    e.preventDefault();
                    await this.handleFormSubmit(e.target);
                }
            });

            // Textarea handling
            document.addEventListener('keydown', (e) => {
                if (e.target.matches('#message-container textarea[name="new_message"]') && e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    const form = document.getElementById('message-form');
                    if (form) form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                }
            });
        }

        async loadMessages(threadId) {
            try {
                const params = threadId ? `?thread_id=${threadId}` : '';
                const response = await fetch(`${window.NovaApp.urls.messageList}${params}`, { headers: { 'X-AJAX': 'true' } });

                if (response.status === 404 && threadId) {
                    return this.loadMessages(null);
                }

                const html = await response.text();
                document.getElementById('message-container').innerHTML = html;
                // Thread id can be implicit (when threadId param omitted).
                // Always re-sync from the hidden input rendered by the server.
                const renderedThreadId = document.querySelector('#message-container input[name="thread_id"]')?.value;
                this.currentThreadId = renderedThreadId || threadId;

                document.querySelectorAll('.thread-link').forEach(a => a.classList.remove('active'));
                const active = document.querySelector(`.thread-link[data-thread-id="${this.currentThreadId}"]`);
                if (active) active.classList.add('active');

                // No browser persistence of selected thread.

                // Announce thread change so other modules (Files panel, Preview split) can react
                document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: this.currentThreadId || null } }));

                this.initTextareaFocus();
                // Update voice button visibility based on browser support
                this.updateVoiceButtonState();
                // Auto-scroll to bottom for new conversations
                this.scrollToBottom();

                // Handle server-rendered interaction cards and check for pending interactions
                this.checkPendingInteractions();

                // Update compact link visibility for existing messages
                this.updateCompactLinkVisibility();

                // Check for running tasks and reconnect to streaming if needed
                this.checkAndReconnectRunningTasks();

                // If nothing is running and there are no pending interactions,
                // ensure the input is enabled (important after a page reload).
                const pendingCards = document.querySelectorAll('[data-interaction-id]');
                if (!pendingCards || pendingCards.length === 0) {
                    this.streamingManager.setInputAreaDisabled(false);
                }
            } catch (error) {
                console.error('Error loading messages:', error);
            }
        }


        async answerInteraction(interactionId, answer) {
            const clickedBtn = document.querySelector(`.interaction-answer-btn[data-interaction-id="${interactionId}"]`);
            if (!clickedBtn || clickedBtn.disabled) return;
            const originalHtml = clickedBtn.innerHTML;
            clickedBtn.disabled = true;
            clickedBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> ' + gettext('Processing…');
            try {
                const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.interactionAnswer.replace('0', interactionId), {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ answer: answer || '' })
                });
                if (!response.ok) throw new Error('Server error');
                const data = await response.json();
                // Re-enable main input
                this.streamingManager.setInputAreaDisabled(false);
            } catch (error) {
                console.error('Error answering interaction:', error);
                clickedBtn.disabled = false;
                clickedBtn.innerHTML = originalHtml;
            }
        }

        async cancelInteraction(interactionId) {
            const clickedBtn = document.querySelector(`.interaction-cancel-btn[data-interaction-id="${interactionId}"]`);
            if (!clickedBtn || clickedBtn.disabled) return;
            const originalHtml = clickedBtn.innerHTML;
            clickedBtn.disabled = true;
            clickedBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> ' + gettext('Processing…');
            try {
                const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.interactionCancel.replace('0', interactionId), { method: 'POST' });
                if (!response.ok) throw new Error('Server error');
                const data = await response.json();
                // Re-enable main input
                this.streamingManager.setInputAreaDisabled(false);
            } catch (error) {
                console.error('Error canceling interaction:', error);
                clickedBtn.disabled = false;
                clickedBtn.innerHTML = originalHtml;
            }
        }

        async handleFormSubmit(form) {
            const textarea = form.querySelector('textarea[name="new_message"]');
            const msg = textarea ? textarea.value.trim() : '';
            if (!msg) return;

            // Disable send button
            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) {
                sendBtn.disabled = true;
                sendBtn.innerHTML = '<i class="bi bi-hourglass-split"></i>';
            }

            try {
                // Send the message to the server
                const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.addMessage, {
                    method: 'POST',
                    body: new FormData(form)
                });

                const data = await response.json();
                if (data.status !== "OK") throw new Error(data.message || "Failed to send message");

                // Update thread ID if new thread was created
                const threadIdInput = document.querySelector('input[name="thread_id"]');
                if (threadIdInput) threadIdInput.value = data.thread_id;
                this.currentThreadId = data.thread_id;

                // Add user message dynamically on the page
                const userMessageEl = window.MessageRenderer.createMessageElement(data.message, '');
                this.appendMessage(userMessageEl);

                // Scroll to position the message at the top
                this.scrollToMessage(data.message.id);

                // Register streaming for agent response (this will disable input area)
                this.streamingManager.registerStream(data.task_id, {
                    id: data.task_id,
                    actor: 'agent',
                    text: ''
                });

                // Clear textarea
                if (textarea) {
                    textarea.value = '';
                    textarea.dispatchEvent(new Event('input')); // Force resize to min height
                }
            } catch (error) {
                console.error("Error sending message:", error);
                // Re-enable send button on error
                if (sendBtn) {
                    sendBtn.disabled = false;
                    sendBtn.innerHTML = '<i class="bi bi-send-fill"></i>';
                }
            }
        }

        appendMessage(messageElement) {
            const messagesList = document.getElementById('messages-list');
            if (messagesList) {
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

        async createThread() {
            try {
                const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.createThread, { method: 'POST' });
                const data = await response.json();
                if (data.threadHtml) {
                    // Use the threads-list container instead of threads-container
                    const container = document.getElementById('threads-list');
                    const todayGroup = window.ThreadManager.UIUtils.ensureGroupContainer('today', container);
                    const ul = todayGroup ? todayGroup.querySelector('ul.list-group') : null;
                    if (ul) {
                        ul.insertAdjacentHTML('afterbegin', data.threadHtml);
                    }
                }
                this.loadMessages(data.thread_id);
                // Dispatch custom event for thread change
                document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: data.thread_id } }));
            } catch (error) {
                console.error('Error creating thread:', error);
            }
        }

        async deleteThread(threadId) {
            try {
                await window.DOMUtils.csrfFetch(window.NovaApp.urls.deleteThread.replace('0', threadId), { method: 'POST' });
                const threadElement = document.getElementById(`thread-item-${threadId}`);
                if (threadElement) threadElement.remove();

                // Determine next thread to show (if any) before removal
                const firstThread = document.querySelector('.thread-link');
                const firstThreadId = firstThread?.dataset.threadId;
                this.loadMessages(firstThreadId);
                // Dispatch custom event for thread change (null if no threads left)
                document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: firstThreadId || null } }));
            } catch (error) {
                console.error('Error deleting thread:', error);
            }
        }

        async summarizeCurrentThread() {
            if (!this.currentThreadId) {
                alert('No thread selected');
                return;
            }

            const compactLinks = document.querySelectorAll('.compact-thread-link');
            if (compactLinks.length === 0) return;

            // Store original HTML for all links
            const originalHtmls = Array.from(compactLinks).map(link => link.innerHTML);

            // Update all compact links to loading state
            compactLinks.forEach(link => {
                link.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>' + gettext('Starting...');
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
                    // Show confirmation dialog for sub-agents
                    this.showSubAgentConfirmationDialog(data.sub_agents, data.thread_id);

                    // Reset compact links
                    compactLinks.forEach((link, index) => {
                        link.innerHTML = originalHtmls[index];
                        link.style.pointerEvents = '';
                        link.style.opacity = '';
                    });
                } else if (data.status === 'OK' && data.task_id) {
                    // Register streaming for the summarization task (this will disable input area)
                    this.streamingManager.registerStream(data.task_id, {
                        id: data.task_id,
                        actor: 'system',  // Summarization is a system operation
                        text: ''
                    });

                    // Update compact links to show it's running
                    compactLinks.forEach(link => {
                        link.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>' + gettext('Running...');
                    });

                    // Note: Links will be reset when streaming completes via WebSocket events
                } else {
                    throw new Error(data.message || 'Summarization failed');
                }
            } catch (error) {
                console.error('Error summarizing thread:', error);

                // Reset all links on error
                compactLinks.forEach((link, index) => {
                    link.innerHTML = originalHtmls[index];
                    link.style.pointerEvents = '';
                    link.style.opacity = '';
                });

                // Show error message
                alert('Failed to start summarization: ' + error.message);
            }
        }

        showSubAgentConfirmationDialog(subAgents, threadId) {
            // Populate the modal
            const list = document.getElementById('subAgentList');
            if (list) {
                list.innerHTML = subAgents.map(agent =>
                    `<li class="list-group-item">${agent.name} (${agent.token_count} ${gettext('tokens')})</li>`
                ).join('');
            }

            // Store data for confirmation
            const modal = document.getElementById('subAgentConfirmationModal');
            if (modal) {
                modal.dataset.threadId = threadId;
                modal.dataset.subAgents = JSON.stringify(subAgents);

                // Show the modal
                const bsModal = new bootstrap.Modal(modal);
                bsModal.show();
            }
        }

        async confirmSummarize(includeSubAgents) {
            // Get data from modal
            const modal = document.getElementById('subAgentConfirmationModal');
            if (!modal) return;

            const threadId = modal.dataset.threadId;
            const subAgents = JSON.parse(modal.dataset.subAgents || '[]');
            const subAgentIds = includeSubAgents ? subAgents.map(a => a.id) : [];

            // Hide the modal
            const bsModal = bootstrap.Modal.getInstance(modal);
            if (bsModal) {
                bsModal.hide();
            }

            // Show loading state on compact links
            const compactLinks = document.querySelectorAll('.compact-thread-link');
            compactLinks.forEach(link => {
                link.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>' + gettext('Starting...');
                link.style.pointerEvents = 'none';
                link.style.opacity = '0.6';
            });

            try {
                const response = await window.DOMUtils.csrfFetch(
                    window.NovaApp.urls.confirmSummarizeThread.replace('0', threadId),
                    {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: new URLSearchParams({
                            'include_sub_agents': includeSubAgents,
                            'sub_agent_ids': JSON.stringify(subAgentIds)
                        })
                    }
                );

                const data = await response.json();

                if (data.status === 'OK' && data.task_id) {
                    // Register streaming for the summarization task
                    this.streamingManager.registerStream(data.task_id, {
                        id: data.task_id,
                        actor: 'system',
                        text: ''
                    });

                    // Update compact links to show it's running
                    compactLinks.forEach(link => {
                        link.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>' + gettext('Running...');
                    });
                } else {
                    throw new Error(data.message || 'Summarization failed');
                }
            } catch (error) {
                console.error('Error confirming summarization:', error);

                // Reset compact links on error
                compactLinks.forEach(link => {
                    link.innerHTML = '<i class="bi bi-compress me-1"></i>' + gettext('Compact');
                    link.style.pointerEvents = '';
                    link.style.opacity = '';
                });

                alert('Failed to start summarization: ' + error.message);
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

        loadInitialThread() {
            // Default behavior: server decides which thread to show when none is selected.
            this.loadMessages(null);
        }

        // Disable main input if there are pending interactions
        checkPendingInteractions() {
            const pendingCards = document.querySelectorAll('[data-interaction-id]');
            if (pendingCards.length > 0) {
                this.streamingManager.setInputAreaDisabled(true);
            }
        }

        // Check for running tasks and reconnect to streaming if needed
        async checkAndReconnectRunningTasks() {
            if (!this.currentThreadId) return;

            try {
                const response = await fetch(
                    `${window.NovaApp.urls.runningTasksBase}${this.currentThreadId}/`
                );
                const data = await response.json();

                if (data.running_tasks && data.running_tasks.length > 0) {
                    // Reconnect to each running task with state
                    for (const task of data.running_tasks) {
                        this.streamingManager.reconnectToTask(
                            task.id,
                            task.current_response,
                            task.last_progress
                        );
                    }
                } else {
                    // No running tasks: hide progress UI and re-enable input.
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
        }

        // Initialize voice recognition
        initVoiceRecognition() {
            if (typeof window.VoiceRecognitionManager !== 'undefined') {
                this.voiceRecognition = new window.VoiceRecognitionManager();

                // Set up voice recognition callbacks
                this.voiceRecognition.onResult((transcript, isInterim) => {
                    this.handleVoiceResult(transcript, isInterim);
                });

                this.voiceRecognition.onError((error) => {
                    this.handleVoiceError(error);
                });

                this.voiceRecognition.onStart(() => {
                    this.handleVoiceStart();
                });

                this.voiceRecognition.onEnd(() => {
                    this.handleVoiceEnd();
                });

                // Note: updateVoiceButtonState() is called in loadMessages() after DOM is ready
            }
        }

        // Handle voice button click
        handleVoiceButtonClick() {
            if (!this.voiceRecognition) {
                console.error('Voice recognition not initialized');
                return;
            }

            const status = this.voiceRecognition.getStatus();

            if (!status.isSupported) {
                alert('Voice recognition is not supported in this browser. Please use a modern browser like Chrome, Edge, or Safari.');
                return;
            }

            if (status.isListening) {
                // Stop listening
                this.voiceRecognition.stop();
            } else {
                // Start listening
                try {
                    this.voiceRecognition.start();
                } catch (error) {
                    console.error('Error starting voice recognition:', error);
                    this.showVoiceError('Failed to start voice recognition. Please check microphone permissions.');
                }
            }
        }

        // Handle voice recognition result
        handleVoiceResult(transcript, isInterim) {
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (!textarea) return;

            // Update textarea with transcript
            textarea.value = transcript;

            // Trigger input event to update textarea height
            textarea.dispatchEvent(new Event('input', { bubbles: true }));

            // Focus the textarea
            textarea.focus();

            // If this is final result, enable send button
            if (!isInterim) {
                const sendBtn = document.getElementById('send-btn');
                if (sendBtn && !sendBtn.disabled) {
                    // Auto-submit if transcript is not empty
                    if (transcript.trim()) {
                        const form = document.getElementById('message-form');
                        if (form) {
                            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                        }
                    }
                }
            }
        }

        // Handle voice recognition error
        handleVoiceError(error) {
            console.error('Voice recognition error:', error);
            let message = 'Voice recognition error occurred.';

            switch (error) {
                case 'not-allowed':
                    message = 'Microphone access denied. Please allow microphone access and try again.';
                    break;
                case 'no-speech':
                    message = 'No speech detected. Please try speaking again.';
                    break;
                case 'audio-capture':
                    message = 'Audio capture failed. Please check your microphone.';
                    break;
                case 'network':
                    message = 'Network error during voice recognition.';
                    break;
                default:
                    message = `Voice recognition error: ${error}`;
            }

            this.showVoiceError(message);
        }

        // Handle voice recognition start
        handleVoiceStart() {
            this.updateVoiceButtonState(true);
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (textarea) {
                textarea.placeholder = 'Listening... Speak now';
            }
        }

        // Handle voice recognition end
        handleVoiceEnd() {
            this.updateVoiceButtonState(false);
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (textarea) {
                textarea.placeholder = 'Type your message...';
            }
        }

        // Update voice button visual state
        updateVoiceButtonState(isListening = false) {
            const voiceBtn = document.getElementById('voice-btn');
            if (!voiceBtn) return;

            // Hide button if voice recognition is not supported
            if (this.voiceRecognition && !this.voiceRecognition.getStatus().isSupported) {
                voiceBtn.style.display = 'none';
                return;
            }

            // Show button if supported
            voiceBtn.style.display = '';

            const icon = voiceBtn.querySelector('i');
            if (!icon) return;

            if (isListening) {
                voiceBtn.classList.remove('btn-outline-secondary');
                voiceBtn.classList.add('btn-danger');
                icon.className = 'bi bi-mic-fill text-white';
                voiceBtn.title = 'Stop voice input';
            } else {
                voiceBtn.classList.remove('btn-danger');
                voiceBtn.classList.add('btn-outline-secondary');
                icon.className = 'bi bi-mic';
                voiceBtn.title = 'Voice input';
            }
        }

        // Show voice error message
        showVoiceError(message) {
            // For now, just log to console and show alert
            // In a production app, you might want to show a toast notification
            console.warn('Voice recognition:', message);
            alert(message);
        }

        // ============================================================================
        // LONG PRESS CONTEXT MENU (Mobile only)
        // ============================================================================

        initLongPressContextMenu() {
            // Only initialize on touch devices
            if (!('ontouchstart' in window)) return;

            const conversationContainer = document.getElementById('conversation-container');
            if (!conversationContainer) return;

            // Initialize Bootstrap offcanvas
            const offcanvasEl = document.getElementById('messageContextMenu');
            if (offcanvasEl) {
                this.contextMenuOffcanvas = new bootstrap.Offcanvas(offcanvasEl);
            }

            // Touch event listeners on conversation container (event delegation)
            conversationContainer.addEventListener('touchstart', (e) => this.handleTouchStart(e), { passive: true });
            conversationContainer.addEventListener('touchend', (e) => this.handleTouchEnd(e));
            conversationContainer.addEventListener('touchmove', (e) => this.handleTouchMove(e), { passive: true });
            conversationContainer.addEventListener('touchcancel', (e) => this.handleTouchCancel(e));

            // Context menu action handlers
            this.initContextMenuActions();
        }

        handleTouchStart(e) {
            const messageCard = e.target.closest('.message .card');
            if (!messageCard) return;

            // Store starting position to detect scroll
            const touch = e.touches[0];
            this.touchStartPos = { x: touch.clientX, y: touch.clientY };
            this.longPressTarget = messageCard;

            // Add visual feedback class
            const messageEl = messageCard.closest('.message');

            // Start long press timer
            this.longPressTimer = setTimeout(() => {
                if (messageEl) messageEl.classList.add('long-press-active');

                // Trigger haptic feedback if available
                if (window.navigator && window.navigator.vibrate) {
                    window.navigator.vibrate(50);
                }

                // Show context menu after a short delay for visual feedback
                setTimeout(() => {
                    if (messageEl) messageEl.classList.remove('long-press-active');
                    this.showMessageContextMenu(messageCard);
                }, 100);
            }, this.longPressDuration);
        }

        handleTouchEnd(e) {
            this.cancelLongPress();
        }

        handleTouchMove(e) {
            if (!this.touchStartPos) return;

            const touch = e.touches[0];
            const dx = Math.abs(touch.clientX - this.touchStartPos.x);
            const dy = Math.abs(touch.clientY - this.touchStartPos.y);

            // Cancel long press if user scrolls (threshold: 10px)
            if (dx > 10 || dy > 10) {
                this.cancelLongPress();
            }
        }

        handleTouchCancel(e) {
            this.cancelLongPress();
        }

        cancelLongPress() {
            if (this.longPressTimer) {
                clearTimeout(this.longPressTimer);
                this.longPressTimer = null;
            }

            // Remove visual feedback
            const activeEl = document.querySelector('.message.long-press-active');
            if (activeEl) activeEl.classList.remove('long-press-active');

            this.touchStartPos = null;
        }

        showMessageContextMenu(messageCard) {
            if (!this.contextMenuOffcanvas) return;

            const messageEl = messageCard.closest('.message');
            if (!messageEl) return;

            // Store reference to current message for actions
            this.currentContextMessage = messageEl;

            // Extract message content for copy
            const cardBody = messageCard.querySelector('.card-body');
            this.currentMessageText = cardBody ? cardBody.textContent.trim() : '';

            // Check if this is the last agent message (for compact/regenerate options)
            const isAgentMessage = messageCard.classList.contains('border-secondary');
            const isLastMessage = this.isLastAgentMessage(messageEl);

            // Update context info if available
            const contextInfo = document.getElementById('context-menu-info');
            const tokensEl = document.getElementById('context-menu-tokens');

            // Try to find context info in the hidden card-footer
            const cardFooter = messageCard.querySelector('.card-footer-consumption');
            if (cardFooter && cardFooter.textContent.trim()) {
                // Parse context info from footer text
                const footerText = cardFooter.textContent.trim();
                if (tokensEl) tokensEl.textContent = footerText.replace('Context consumption:', '').trim();
                if (contextInfo) contextInfo.classList.remove('d-none');
            } else {
                if (contextInfo) contextInfo.classList.add('d-none');
            }

            // Show/hide regenerate button (only for last agent message)
            const regenerateBtn = document.getElementById('context-menu-regenerate');
            if (regenerateBtn) {
                regenerateBtn.classList.toggle('d-none', !isAgentMessage || !isLastMessage);
            }


            // Show the offcanvas
            this.contextMenuOffcanvas.show();
        }

        isLastAgentMessage(messageEl) {
            const messagesList = document.getElementById('messages-list');
            if (!messagesList) return false;

            // Get all agent messages
            const agentMessages = messagesList.querySelectorAll('.message .card.border-secondary');
            if (agentMessages.length === 0) return false;

            // Check if this message contains the last agent message card
            const lastAgentCard = agentMessages[agentMessages.length - 1];
            return messageEl.contains(lastAgentCard);
        }

        initContextMenuActions() {
            // Copy message
            const copyBtn = document.getElementById('context-menu-copy');
            if (copyBtn) {
                copyBtn.addEventListener('click', () => {
                    this.copyMessageToClipboard();
                    this.contextMenuOffcanvas.hide();
                });
            }

            // Regenerate response (placeholder - would need backend support)
            const regenerateBtn = document.getElementById('context-menu-regenerate');
            if (regenerateBtn) {
                regenerateBtn.addEventListener('click', () => {
                    // TODO: Implement regenerate functionality
                    console.log('Regenerate not yet implemented');
                    this.contextMenuOffcanvas.hide();
                });
            }

        }

        async copyMessageToClipboard() {
            if (!this.currentMessageText) return;

            try {
                await navigator.clipboard.writeText(this.currentMessageText);
                // Optional: Show brief success feedback
                console.log('Message copied to clipboard');
            } catch (err) {
                console.error('Failed to copy message:', err);
                // Fallback for older browsers
                this.fallbackCopyToClipboard(this.currentMessageText);
            }
        }

        fallbackCopyToClipboard(text) {
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-9999px';
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
            } catch (err) {
                console.error('Fallback copy failed:', err);
            }
            document.body.removeChild(textArea);
        }
    };
})();
