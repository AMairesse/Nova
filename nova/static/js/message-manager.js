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
        }

        init() {
            // Attach event handlers
            this.attachEventHandlers();
            this.loadInitialThread();

            // Handle server-rendered interaction cards and check for pending interactions
            this.checkPendingInteractions();
        }

        attachEventHandlers() {
            // 'click' event mapping
            const eventMappings = {
                '.thread-link': (e, target) => {
                    e.preventDefault();
                    const link = target.closest('.thread-link');
                    const threadId = link.dataset.threadId;
                    this.loadMessages(threadId);
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
                '.compact-thread-btn': (e, target) => {
                    e.preventDefault();
                    const btn = target.closest('.compact-thread-btn');
                    const threadId = btn.dataset.threadId;
                    this.compactThread(threadId, btn);
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
                    window.StorageUtils.setItem('lastThreadId', null);
                    return this.loadMessages(null);
                }

                const html = await response.text();
                document.getElementById('message-container').innerHTML = html;
                this.currentThreadId = threadId;

                document.querySelectorAll('.thread-link').forEach(a => a.classList.remove('active'));
                const active = document.querySelector(`.thread-link[data-thread-id="${this.currentThreadId}"]`);
                if (active) active.classList.add('active');

                if (threadId) {
                    window.StorageUtils.setItem('lastThreadId', threadId);
                }

                // Announce thread change so other modules (Files panel, Preview split) can react
                document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: threadId || null } }));

                this.initTextareaFocus();
                // Update voice button visibility based on browser support
                this.updateVoiceButtonState();
                // Auto-scroll to bottom for new conversations
                this.scrollToBottom();

                // Handle server-rendered interaction cards and check for pending interactions
                this.checkPendingInteractions();
            } catch (error) {
                console.error('Error loading messages:', error);
            }
        }

        async compactThread(threadId, btnEl) {
            const clickedBtn = btnEl || document.querySelector(`.compact-thread-btn[data-thread-id="${threadId}"]`);
            if (!clickedBtn || clickedBtn.disabled) return;
            const originalHtml = clickedBtn.innerHTML;
            clickedBtn.disabled = true;
            clickedBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> ' + gettext('Processing…');
            try {
                const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.compactThread.replace('0', threadId), { method: 'POST' });
                if (!response.ok) throw new Error('Server error');
                const data = await response.json();
                if (data.task_id) this.streamingManager.registerBackgroundTask(data.task_id);
            } catch (error) {
                console.error('Error compacting thread:', error);
                clickedBtn.disabled = false;
                clickedBtn.innerHTML = originalHtml;
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

                // Register streaming for agent response
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
            } finally {
                // Re-enable send button
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

        async createThread() {
            try {
                const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.createThread, { method: 'POST' });
                const data = await response.json();
                if (data.threadHtml) {
                    // Use the threads-list container instead of threads-container
                    const container = document.getElementById('threads-list');
                    const todayGroup = ensureGroupContainer('today', container);
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
                if (window.StorageUtils.getItem('lastThreadId') === threadId.toString()) {
                    window.StorageUtils.setItem('lastThreadId', null);
                }
                // Dispatch custom event for thread change (null if no threads left)
                document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: firstThreadId || null } }));
            } catch (error) {
                console.error('Error deleting thread:', error);
            }
        }

        loadInitialThread() {
            const lastThreadId = window.StorageUtils.getItem('lastThreadId');
            this.loadMessages(lastThreadId);
        }

        // Disable main input if there are pending interactions
        checkPendingInteractions() {
            const pendingCards = document.querySelectorAll('[data-interaction-id]');
            if (pendingCards.length > 0) {
                this.streamingManager.setInputAreaDisabled(true);
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
    };

    // Thread UI helpers for grouping and DOM manipulation
    function getGroupOrder() {
        return ['today', 'yesterday', 'last_week', 'last_month', 'older'];
    }
    function getGroupTitle(key) {
        const t = (typeof window.gettext === 'function') ? window.gettext : (s) => s;
        switch (key) {
            case 'today': return t('Today');
            case 'yesterday': return t('Yesterday');
            case 'last_week': return t('Last Week');
            case 'last_month': return t('Last Month');
            default: return t('Older');
        }
    }
    function ensureGroupContainer(group, containerEl) {
        // Use the threads-list container instead of threads-container
        const container = containerEl || document.getElementById('threads-list');
        if (!container) return null;

        let grp = container.querySelector(`.thread-group[data-group="${group}"]`);
        if (!grp) {
            grp = document.createElement('div');
            grp.className = 'thread-group mb-3';
            grp.setAttribute('data-group', group);

            const h6 = document.createElement('h6');
            h6.className = 'text-muted mb-2 px-3 pt-2 pb-1 border-bottom';
            h6.textContent = getGroupTitle(group);

            const ul = document.createElement('ul');
            ul.className = 'list-group list-group-flush';

            grp.appendChild(h6);
            grp.appendChild(ul);

            // Insert in correct order
            const order = getGroupOrder();
            const targetIndex = order.indexOf(group);
            let insertBefore = null;
            // Ensure groups is defined (it may not exist if no groups yet)
            const groups = Array.from(container.querySelectorAll('.thread-group'));
            for (const g of groups) {
                const idx = order.indexOf(g.dataset.group || 'older');
                if (idx > targetIndex) {
                    insertBefore = g;
                    break;
                }
            }
            container.insertBefore(grp, insertBefore);
        }
        return grp;
    }
    function mergeThreadGroupsFromHtml(html, containerEl) {
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        const incomingGroups = tmp.querySelectorAll('.thread-group');
        incomingGroups.forEach(incoming => {
            const group = incoming.dataset.group || 'older';

            // First, try to find existing group in the container
            let targetGroup = containerEl.querySelector(`.thread-group[data-group="${group}"]`);

            // If group doesn't exist, create it using ensureGroupContainer
            if (!targetGroup) {
                targetGroup = ensureGroupContainer(group, containerEl);
            }

            if (!targetGroup) return;

            const incomingUl = incoming.querySelector('ul.list-group');
            const targetUl = targetGroup.querySelector('ul.list-group');
            if (!incomingUl || !targetUl) return;

            // Append all new threads to the existing group
            while (incomingUl.firstElementChild) {
                targetUl.appendChild(incomingUl.firstElementChild);
            }
        });
    }

})();