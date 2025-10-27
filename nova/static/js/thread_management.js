/* nova/static/js/thread_management.js - Modern chat architecture */
(function() {
  'use strict';

  // Configuration object for URLs (will be populated from template)
  window.NovaApp = window.NovaApp || {};

  // ============================================================================
  // MESSAGE RENDERER - Unified conversion for consistency
  // ============================================================================
  class MessageRenderer {
    static createMessageElement(messageData, thread_id) {
      const messageDiv = document.createElement('div');
      messageDiv.className = 'message mb-3';
      messageDiv.id = `message-${messageData.id}`;
      messageDiv.setAttribute('data-message-id', messageData.id);

      if (messageData.actor === 'SYS' || messageData.actor === 'system') {
        return this.createSystemMessageElement(messageData);
      } else if (messageData.actor === 'user' || messageData.actor === 'USR') {
        messageDiv.innerHTML = `
          <div class="card border-primary">
            <div class="card-body py-2">
              <strong class="text-primary">${messageData.text}</strong>
              ${messageData.file_count ? `<div class="mt-2 small text-muted">${messageData.file_count} file(s) attached</div>` : ''}
            </div>
          </div>
        `;
      } else if (messageData.actor === 'agent') {
        // Remove "compact" button from previous message footer
        const button_to_remove = document.querySelector('.compact-thread-btn');
        if (button_to_remove) {
          button_to_remove.remove();
        }
        // Agent message structure
        messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content">${messageData.text}</div>
            </div>
            <div class="card-footer py-1 text-muted small text-end d-none d-flex justify-content-end align-items-center">
              <div class="card-footer-consumption">
              </div>
              <button
                type="button"
                class="btn btn-link btn-sm text-decoration-none compact-thread-btn"
                data-thread-id="`+ thread_id + `"
              >
                <i class="bi bi-filter-circle me-1"></i>` + gettext('Compact') + `
              </button>
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
      if (messageData.internal_data && messageData.internal_data.type === 'compact_complete') {
        messageDiv.innerHTML = `
          <div class="card border-light">
            <div class="card-body py-2">
              <div class="text-muted small">
                ${messageData.text}
                <button
                  class="btn btn-sm text-muted p-0 ms-1 border-0 bg-transparent"
                  type="button"
                  onclick="toggleCompactDetails(this)"
                  data-collapsed="true"
                  title="Show summary details"
                >
                  <small>[+ details]</small>
                </button>
              </div>
              <div class="compact-details mt-2 d-none">
                <div class="border-start border-secondary ps-2">
                  <small class="text-muted">${messageData.internal_data.summary || ''}</small>
                </div>
              </div>
            </div>
          </div>
        `;
      } else {
        // Fallback for other system messages
        messageDiv.innerHTML = `
          <div class="card border-light">
            <div class="card-body py-2">
              <div class="text-muted small">${messageData.text}</div>
            </div>
          </div>
        `;
      }

      return messageDiv;
    }
  }

  // ============================================================================
  // STREAMING MANAGER - Coordinates WebSocket and message streaming
  // ============================================================================
  class StreamingManager {
    constructor() {
      this.activeStreams = new Map(); // taskId -> stream data
      this.messageManager = null;
      this.awaitingUserAnswer = false; // NEW: track if UI is paused awaiting answer
    }

    setMessageManager(manager) {
      this.messageManager = manager;
    }

    registerStream(taskId, messageData, thread_id) {
      const agentMessageEl = MessageRenderer.createMessageElement({
        ...messageData,
        actor: 'agent',
        text: '' // Start with empty content
      }, thread_id);

      // Add streaming class to the message container for proper CSS targeting
      agentMessageEl.classList.add('streaming');

      this.messageManager.appendMessage(agentMessageEl);

      this.activeStreams.set(taskId, {
        messageId: messageData.id,
        element: agentMessageEl,
        currentText: '',
        lastUpdate: Date.now()
      });

      // Show progress area when streaming starts (ensure it's visible)
      const progressDiv = document.getElementById('task-progress');
      if (progressDiv) {
        progressDiv.classList.remove('d-none');
        // Also ensure spinner is visible for new tasks
        const spinner = progressDiv.querySelector('.spinner-border');
        if (spinner) {
          spinner.classList.remove('d-none');
        }
      }

      // Start WebSocket connection
      this.startWebSocket(taskId);
    }

    onStreamChunk(taskId, chunk) {
      // Guard: if a chunk arrives after a user prompt, ensure the input is re-enabled
      if (this.awaitingUserAnswer) {
        this.setInputAreaDisabled(false);
        this.awaitingUserAnswer = false;
      }
      
      const stream = this.activeStreams.get(taskId);
      if (!stream) return;

      // Skip duplicate chunks (server sometimes sends the same content multiple times)
      // Also skip empty chunks
      if (!chunk || chunk.trim() === '' || chunk === stream.lastChunk) {
        return;
      }

      // Warning :for system action (eg. "compact"), there is no element for streaming
      if (!stream.element) {
        return
      }

      // The server is already sending HTML chunks, so we don't need to process them as Markdown
      // Replace the entire content since server sends complete paragraph updates
      const contentEl = stream.element.querySelector('.streaming-content');
      if (contentEl) {
        contentEl.innerHTML = chunk;
      }

      // Still accumulate text for state management
      stream.currentText += chunk;
      stream.lastChunk = chunk; // Track last chunk to detect duplicates
      stream.lastUpdate = Date.now();
    }

    onStreamComplete(taskId) {
      const stream = this.activeStreams.get(taskId);
      if (stream) {
        // Mark as completed
        stream.status = 'completed';
        // Remove completed stream from localStorage instead of saving
        localStorage.removeItem(`stream_${taskId}`);

        // Immediately hide the spinner when task completes
        const spinner = document.querySelector('#task-progress .spinner-border');
        if (spinner) {
          spinner.classList.add('d-none');
        }

        // Hide entire progress area after a delay
        const progressDiv = document.getElementById('task-progress');
        if (progressDiv) {
          setTimeout(() => {
            progressDiv.classList.add('d-none');
          }, 3000); // Hide progress after 3 seconds
        }
      }
      this.activeStreams.delete(taskId);
    }

    saveStreamState(taskId, stream) {
      // Limit currentText to last 50KB to prevent large individual entries
      const maxTextLength = 50 * 1024; // 50KB
      const currentText = stream.currentText.length > maxTextLength
        ? stream.currentText.slice(-maxTextLength)
        : stream.currentText;

      const state = {
        messageId: stream.messageId,
        currentText: currentText,
        lastUpdate: stream.lastUpdate,
        status: stream.status || 'streaming'
      };

      try {
        localStorage.setItem(`stream_${taskId}`, JSON.stringify(state));
      } catch (e) {
        if (e.name === 'QuotaExceededError') {
          console.warn('localStorage quota exceeded, running cleanup and retrying...');
          // Run cleanup to free up space
          this.cleanupStreams();
          try {
            // Retry after cleanup
            localStorage.setItem(`stream_${taskId}`, JSON.stringify(state));
            console.log('Successfully saved stream state after cleanup');
          } catch (retryError) {
            console.error('Failed to save stream state even after cleanup:', retryError);
            // Continue execution - streaming will still work, just won't resume on page reload
          }
        } else {
          console.error('Error saving stream state:', e);
        }
      }
    }

    loadSavedStreams() {
      const streams = {};
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.startsWith('stream_')) {
          const taskId = key.replace('stream_', '');
          try {
            streams[taskId] = JSON.parse(localStorage.getItem(key));
          } catch (e) {
            console.warn('Invalid stream state:', key);
          }
        }
      }
      return streams;
    }

    cleanupStreams() {
      const now = Date.now();
      const twoDaysMs = 2 * 24 * 60 * 60 * 1000; // 2 days in milliseconds
      const keysToRemove = [];

      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key.startsWith('stream_')) {
          try {
            const state = JSON.parse(localStorage.getItem(key));
            // Remove completed streams or streams older than 2 days
            if (state.status === 'completed' || (state.lastUpdate && now - state.lastUpdate > twoDaysMs)) {
              keysToRemove.push(key);
            }
          } catch (e) {
            // Remove invalid entries
            keysToRemove.push(key);
          }
        }
      }

      keysToRemove.forEach(key => localStorage.removeItem(key));

      if (keysToRemove.length > 0) {
        console.log(`Cleaned up ${keysToRemove.length} old stream entries from localStorage`);
      }
    }

    startWebSocket(taskId) {
      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${protocol}://${window.location.host}/ws/task/${taskId}/`;

      const socket = new WebSocket(wsUrl);
      let heartbeatInterval, heartbeatTimeout;

      const startHeartbeat = () => {
        clearInterval(heartbeatInterval);
        clearTimeout(heartbeatTimeout);
        heartbeatInterval = setInterval(() => {
          if (socket.readyState === WebSocket.OPEN) {
            socket.send(JSON.stringify({ type: 'ping' }));
            heartbeatTimeout = setTimeout(() => {
              console.error('Heartbeat timeout: Closing WebSocket');
              socket.close(1006, 'Heartbeat timeout');
            }, 10000);
          }
        }, 30000);
      };

      socket.onopen = () => startHeartbeat();

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'pong') {
          clearTimeout(heartbeatTimeout);
          return;
        }

        if (data.type === 'progress_update') {
          const progressLogs = document.getElementById('progress-logs');
          const statusDiv = document.getElementById('task-status');
          const log = data.progress_log || "undefined";
          if (progressLogs) progressLogs.textContent = log;
          if (statusDiv && data.error) {
            statusDiv.innerHTML = '<p class="text-danger">' + data.error + "</p>";
          }
        } else if (data.type === 'response_chunk') {
          this.onStreamChunk(taskId, data.chunk);
        } else if (data.type === 'context_consumption') {
          // Get the card for this message
          const stream = this.activeStreams.get(taskId);
          if (!stream) return;
          // Get the footer in the card
          const streamingFooter = stream.element.querySelector('.card-footer-consumption');
          if (streamingFooter && data.max_context) {
            // Add the context consumption data
            if (data.real_tokens !== null) {
              streamingFooter.innerHTML = `Context consumption: ${data.real_tokens}/${data.max_context} (real)`;
            } else {
              streamingFooter.innerHTML = `Context consumption: ${data.approx_tokens}/${data.max_context} (approximated)`;
            }
            // Display the footer
            streamingFooter.parentElement.classList.remove('d-none');
          }
        } else if (data.type === 'new_message') {
          // Handle real-time message updates (e.g., system messages from completed tasks)
          this.onNewMessage(data.message, data.thread_id);
        } else if (data.type === 'task_complete') {
          // Update thread title in sidebars if backend provided it
          if (data.thread_id && data.thread_subject) {
            const links = document.querySelectorAll(`.thread-link[data-thread-id="${data.thread_id}"]`);
            links.forEach(a => {
              a.textContent = data.thread_subject;
            });
          }
          this.onStreamComplete(taskId);
        } else if (data.type === 'user_prompt') {
          // NEW: show interactive question card
          this.onUserPrompt(taskId, data);
        } else if (data.type === 'interaction_update') {
          // NEW: reflect backend updates (ANSWERED/RESUMING/CANCELED)
          this.onInteractionUpdate(taskId, data);
        }
      };

      socket.onclose = () => {
        clearInterval(heartbeatInterval);
        clearTimeout(heartbeatTimeout);
      };

      socket.onerror = (err) => {
        console.error('WebSocket error:', err);
      };
    }

    resumeStreams() {
      const savedStreams = this.loadSavedStreams();
      Object.keys(savedStreams).forEach(taskId => {
        if (savedStreams[taskId].status !== 'completed') {
          this.startWebSocket(taskId);
        }
      });
    }
  }

  // ============================================================================
  // MESSAGE MANAGER - Handles dynamic message insertion and scroll
  // ============================================================================
  class MessageManager {
    constructor() {
      this.streamingManager = new StreamingManager();
      this.streamingManager.setMessageManager(this);
      this.currentThreadId = null;
    }

    init() {
      // Clean up old localStorage entries on startup
      this.streamingManager.cleanupStreams();
      this.attachEventHandlers();
      this.loadInitialThread();

      // Handle server-rendered interaction cards and check for pending interactions
      this.bindInteractionCards();
      this.checkPendingInteractions();
    }

    attachEventHandlers() {
      // Thread navigation
      document.addEventListener('click', (e) => {
        if (e.target.matches('.thread-link') || e.target.closest('.thread-link')) {
          e.preventDefault();
          const link = e.target.closest('.thread-link');
          const threadId = link.dataset.threadId;
          this.loadMessages(threadId);
        } else if (e.target.matches('.create-thread-btn') || e.target.closest('.create-thread-btn')) {
          e.preventDefault();
          this.createThread();
        } else if (e.target.matches('.delete-thread-btn') || e.target.closest('.delete-thread-btn')) {
          e.preventDefault();
          const btn = e.target.closest('.delete-thread-btn');
          const threadId = btn.dataset.threadId;
          this.deleteThread(threadId);
        } else if (e.target.matches('.agent-dropdown-item') || e.target.closest('.agent-dropdown-item')) {
          e.preventDefault();
          const item = e.target.closest('.agent-dropdown-item');
          const value = item.dataset.value;
          const label = item.textContent.trim();
          const selectedAgentInput = document.getElementById('selectedAgentInput');
          const dropdownButton = document.getElementById('dropdownMenuButton');
          if (selectedAgentInput) selectedAgentInput.value = value;
          if (dropdownButton) dropdownButton.textContent = label;
        } else if (e.target.matches('.compact-thread-btn') || e.target.closest('.compact-thread-btn')) {
          e.preventDefault();
          const btn = e.target.closest('.compact-thread-btn');
          const threadId = btn.dataset.threadId;
          this.compactThread(threadId, btn);
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

      document.addEventListener('input', (e) => {
        if (e.target.matches('#message-container textarea.auto-resize-textarea[name="new_message"]')) {
          e.target.style.height = "38px";
          e.target.style.height = `${e.target.scrollHeight}px`;
        }
      });
    }

    async loadMessages(threadId) {
      try {
        const params = threadId ? `?thread_id=${threadId}` : '';
        const response = await fetch(`${window.NovaApp.urls.messageList}${params}`, { headers: { 'X-AJAX': 'true' } });

        if (response.status === 404 && threadId) {
          localStorage.removeItem('lastThreadId');
          return this.loadMessages(null);
        }

        const html = await response.text();
        document.getElementById('message-container').innerHTML = html;
        this.currentThreadId = threadId;

        document.querySelectorAll('.thread-link').forEach(a => a.classList.remove('active'));
        const active = document.querySelector(`.thread-link[data-thread-id="${this.currentThreadId}"]`);
        if (active) active.classList.add('active');

        this.streamingManager.resumeStreams();

        if (threadId) {
          localStorage.setItem('lastThreadId', threadId);
        }

        this.initTextareaFocus();
        // Auto-scroll to bottom for new conversations
        this.scrollToBottom();

        // Handle server-rendered interaction cards and check for pending interactions
        this.bindInteractionCards();
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

        // Add user message dynamically
        const userMessageEl = MessageRenderer.createMessageElement(data.message, '');
        this.appendMessage(userMessageEl);

        // Scroll to position the message at the top
        this.scrollToMessage(data.message.id);

        // Register streaming for agent response
        this.streamingManager.registerStream(data.task_id, {
          id: data.task_id,
          actor: 'agent',
          text: ''
        }, data.thread_id);

        // Clear textarea
        if (textarea) textarea.value = '';

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
        localStorage.removeItem(`runningTasks_${threadId}`);
        if (localStorage.getItem('lastThreadId') === threadId.toString()) {
          localStorage.removeItem('lastThreadId');
        }
        // Dispatch custom event for thread change (null if no threads left)
        document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: firstThreadId || null } }));
      } catch (error) {
        console.error('Error deleting thread:', error);
      }
    }

    loadInitialThread() {
      const lastThreadId = localStorage.getItem('lastThreadId');
      this.loadMessages(lastThreadId);
    }

    // Handle server-rendered interaction cards
    bindInteractionCards() {
      // Check if URLs are available
      if (!window.NovaApp.urls || !window.NovaApp.urls.interactionAnswer || !window.NovaApp.urls.interactionCancel) {
        console.warn('Interaction URLs not available:', window.NovaApp.urls);
        return;
      }

      // Bind event handlers to server-rendered interaction cards
      document.querySelectorAll('[data-interaction-id]').forEach(card => {
        const interactionId = card.dataset.interactionId;

        // Skip if already bound
        if (card.dataset.bound === 'true') return;

        const answerBtn = card.querySelector('.interaction-answer-btn');
        const cancelBtn = card.querySelector('.interaction-cancel-btn');
        const inputEl = card.querySelector('.interaction-answer-input');
        const statusEl = card.querySelector('.interaction-status');

        if (answerBtn && cancelBtn && inputEl) {
          const setBusy = (busy) => {
            answerBtn.disabled = busy;
            cancelBtn.disabled = busy;
            inputEl.disabled = busy;
          };

          const postJson = async (url, payload) => {
            return window.DOMUtils.csrfFetch(url, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload || {})
            });
          };

          answerBtn.addEventListener('click', async () => {
            const value = inputEl.value.trim();
            if (!value) {
              statusEl.textContent = gettext('Please provide an answer.');
              return;
            }
            setBusy(true);
            statusEl.textContent = gettext('Sending your answer...');
            try {
              const url = window.NovaApp.urls.interactionAnswer.replace('0', String(interactionId));
              console.log('Answer URL:', url); // Debug log
              const resp = await postJson(url, { answer: value });
              if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
              statusEl.textContent = gettext('Answer sent. Resuming...');
              // Disable the card
              setBusy(true);
              // Re-enable main input
              this.streamingManager.setInputAreaDisabled(false);
            } catch (e) {
              console.error('Failed to send answer:', e);
              statusEl.textContent = gettext('Failed to send the answer. Please retry.');
              setBusy(false);
            }
          });

          cancelBtn.addEventListener('click', async () => {
            setBusy(true);
            statusEl.textContent = gettext('Canceling...');
            try {
              const url = window.NovaApp.urls.interactionCancel.replace('0', String(interactionId));
              console.log('Cancel URL:', url); // Debug log
              const resp = await postJson(url, {});
              if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
              statusEl.textContent = gettext('Canceled.');
              // Disable the card and re-enable main input
              setBusy(true);
              this.streamingManager.setInputAreaDisabled(false);
            } catch (e) {
              console.error('Failed to cancel interaction:', e);
              statusEl.textContent = gettext('Failed to cancel. Please retry.');
              setBusy(false);
            }
          });

          // Mark as bound
          card.dataset.bound = 'true';
        }
      });
    }

    // Disable main input if there are pending interactions
    checkPendingInteractions() {
      const pendingCards = document.querySelectorAll('[data-interaction-id]');
      if (pendingCards.length > 0) {
        this.streamingManager.setInputAreaDisabled(true);
        this.streamingManager.awaitingUserAnswer = true;
      }
    }
  }

  // ============================================================================
  // STREAMING MANAGER - Continued (add to existing class)
  // ============================================================================

  // Register background task (non-streaming operations like compact, delete)
  StreamingManager.prototype.registerBackgroundTask = function(taskId) {
    // Don't add visual message element, just track the task and show progress
    this.activeStreams.set(taskId, {
      taskId: taskId,
      isBackground: true,
      lastUpdate: Date.now(),
      status: 'running'
    });

    // Show progress area for background tasks
    const progressDiv = document.getElementById('task-progress');
    if (progressDiv) {
      progressDiv.classList.remove('d-none');
      const spinner = progressDiv.querySelector('.spinner-border');
      if (spinner) {
        spinner.classList.remove('d-none');
      }
      // Set initial progress message
      const progressLogs = document.getElementById('progress-logs');
      if (progressLogs) {
        progressLogs.textContent = "Processing...";
      }
    }

    // Start WebSocket connection for progress updates
    this.startWebSocket(taskId);
  };

  // Handle real-time message updates like system messages
  StreamingManager.prototype.onNewMessage = function(messageData, thread_id) {
    // Create message element for the new message
    const messageElement = MessageRenderer.createMessageElement(messageData, thread_id);

    // Add to message container
    const messagesList = document.getElementById('messages-list');
    if (messagesList) {
      messagesList.appendChild(messageElement);
    } else {
      console.error('Messages list not found for new message');
    }

    // Scroll to bottom to show new message
    const container = document.getElementById('conversation-container');
    if (container) {
      setTimeout(() => {
        container.scrollTo({
          top: container.scrollHeight,
          behavior: 'smooth'
        });
      }, 100);
    }
  };

  // Simple HTML escape to avoid injecting content as HTML
  function escapeHtml(str) {
    if (str == null) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
  }

  // Disable/enable the main input area while waiting for an interaction
  StreamingManager.prototype.setInputAreaDisabled = function(disabled) {
    const textarea = document.querySelector('#message-container textarea[name="new_message"]');
    const sendBtn  = document.getElementById('send-btn');
    if (textarea) {
      textarea.disabled = disabled;
      textarea.placeholder = disabled ? gettext('Waiting for your answer...') : gettext('Type your message...');
    }
    if (sendBtn) {
      sendBtn.disabled = disabled;
    }
  };

  // Render and handle a user prompt card
  StreamingManager.prototype.onUserPrompt = function(taskId, data) {
    // Expected payload: { interaction_id, question, schema, origin_name, thread_id }
    const {
      interaction_id,
      question,
      schema,
      origin_name
    } = data;

    // Check if server-side card already exists
    const existingCard = document.getElementById(`interaction-card-${interaction_id}`);
    if (existingCard) {
      // Server-side card exists, just ensure input is disabled and state is tracked
      this.setInputAreaDisabled(true);
      this.awaitingUserAnswer = true;
      return;
    }

    // Build card element only if server-side card doesn't exist
    const wrapper = document.createElement('div');
    wrapper.className = 'message mb-3';
    wrapper.id = `interaction-card-${interaction_id}`;
    wrapper.setAttribute('data-interaction-id', String(interaction_id));

    const origin = origin_name ? `${escapeHtml(origin_name)} ${gettext('asks')}:` : gettext('Question');
    const schemaHint = (schema && Object.keys(schema).length > 0)
      ? `<div class="form-text text-muted mt-1">${gettext('Answer format may be structured; plain text is also accepted.')}</div>`
      : '';

    wrapper.innerHTML = `
      <div class="card border-warning">
        <div class="card-body">
          <div class="d-flex align-items-center mb-2">
            <i class="bi bi-question-circle text-warning me-2"></i>
            <strong>${origin}</strong>
          </div>
          <div class="mb-2">${escapeHtml(question)}</div>
          <div class="mb-2">
            <textarea class="form-control interaction-answer-input" rows="2" placeholder="${gettext('Type your answer...')}"></textarea>
            ${schemaHint}
          </div>
          <div class="d-flex gap-2">
            <button type="button" class="btn btn-sm btn-primary interaction-answer-btn">
              <i class="bi bi-check2-circle me-1"></i>${gettext('Answer')}
            </button>
            <button type="button" class="btn btn-sm btn-outline-secondary interaction-cancel-btn">
              <i class="bi bi-x-circle me-1"></i>${gettext('Cancel')}
            </button>
            <div class="ms-auto small text-muted interaction-status"></div>
          </div>
        </div>
      </div>
    `;

    // Append to messages and scroll
    this.messageManager.appendMessage(wrapper);
    // Disable main input while awaiting user answer
    this.setInputAreaDisabled(true);
    // Track awaiting state for guard on next chunks
    this.awaitingUserAnswer = true;

    // Bind actions
    const answerBtn = wrapper.querySelector('.interaction-answer-btn');
    const cancelBtn = wrapper.querySelector('.interaction-cancel-btn');
    const inputEl   = wrapper.querySelector('.interaction-answer-input');
    const statusEl  = wrapper.querySelector('.interaction-status');

    const setBusy = (busy) => {
      if (answerBtn) answerBtn.disabled = busy;
      if (cancelBtn) cancelBtn.disabled = busy;
      if (inputEl) inputEl.disabled = busy;
    };

    const postJson = async (url, payload) => {
      return window.DOMUtils.csrfFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {})
      });
    };

    answerBtn?.addEventListener('click', async () => {
      const value = inputEl?.value?.trim();
      if (!value) {
        statusEl.textContent = gettext('Please provide an answer.');
        return;
      }
      setBusy(true);
      statusEl.textContent = gettext('Sending your answer...');
      try {
        const url = window.NovaApp.urls.interactionAnswer.replace('0', String(interaction_id));
        const resp = await postJson(url, { answer: value });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        // We optimistically mark as sent; backend will send interaction_update/RESUMING too
        statusEl.textContent = gettext('Answer sent. Resuming...');
        // Keep the card visible but disabled until we get an update or the task resumes streaming
      } catch (e) {
        console.error('Failed to send answer:', e);
        statusEl.textContent = gettext('Failed to send the answer. Please retry.');
        setBusy(false);
      }
    });

    cancelBtn?.addEventListener('click', async () => {
      setBusy(true);
      statusEl.textContent = gettext('Canceling...');
      try {
        const url = window.NovaApp.urls.interactionCancel.replace('0', String(interaction_id));
        const resp = await postJson(url, {});
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        statusEl.textContent = gettext('Canceled.');
        // Re-enable main input on cancel
        this.setInputAreaDisabled(false);
      } catch (e) {
        console.error('Failed to cancel interaction:', e);
        statusEl.textContent = gettext('Failed to cancel. Please retry.');
        setBusy(false);
      }
    });
  };

  // Reflect backend updates to the interaction card
  StreamingManager.prototype.onInteractionUpdate = function(taskId, data) {
    const { interaction_id, status } = data;
    const card = document.getElementById(`interaction-card-${interaction_id}`);
    if (!card) return;

    const statusEl = card.querySelector('.interaction-status');
    const answerBtn = card.querySelector('.interaction-answer-btn');
    const cancelBtn = card.querySelector('.interaction-cancel-btn');
    const inputEl = card.querySelector('.interaction-answer-input');

    const disableAll = (disabled) => {
      if (answerBtn) answerBtn.disabled = disabled;
      if (cancelBtn) cancelBtn.disabled = disabled;
      if (inputEl) inputEl.disabled = disabled;
    };

    if (status === 'ANSWERED') {
      if (statusEl) statusEl.textContent = gettext('Answer received. Resuming...');
      disableAll(true);
      // Keep main input disabled until we actually resume or complete
    } else if (status === 'RESUMING') {
      if (statusEl) statusEl.textContent = gettext('Resuming...');
      disableAll(true);
      // Re-enable main input as we resume agent streaming
      this.setInputAreaDisabled(false);
      this.awaitingUserAnswer = false; // NEW
      // Optionally collapse the prompt card...
    } else if (status === 'CANCELED') {
      if (statusEl) statusEl.textContent = gettext('Canceled.');
      disableAll(true);
      this.setInputAreaDisabled(false);
      this.awaitingUserAnswer = false; // NEW
    }
  };


  // ============================================================================
  // SYSTEM MESSAGE HANDLERS
  // ============================================================================

  // Helper function to toggle compact details visibility
  function toggleCompactDetails(button) {
    const isCollapsed = button.dataset.collapsed === 'true';
    const messageDiv = button.closest('.message');
    const detailsDiv = messageDiv.querySelector('.compact-details');

    if (isCollapsed) {
      detailsDiv.classList.remove('d-none');
      button.querySelector('small').textContent = '[- details]';
      button.title = 'Hide summary details';
      button.dataset.collapsed = 'false';
    } else {
      detailsDiv.classList.add('d-none');
      button.querySelector('small').textContent = '[+ details]';
      button.title = 'Show summary details';
      button.dataset.collapsed = 'true';
    }
  }

  // Make function globally available for template onclick handlers
  window.toggleCompactDetails = toggleCompactDetails;

  // ============================================================================
  // MAIN INITIALIZATION
  // ============================================================================

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
      const groups = Array.from(container.querySelectorAll('.thread-group'));
      let insertBefore = null;
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

  // ============================================================================
  // THREAD LOADING MANAGER - Handles pagination and grouping
  // ============================================================================
  class ThreadLoadingManager {
    constructor() {
      this.isLoading = false;
    }

    init() {
      this.attachLoadMoreHandlers();
    }

    attachLoadMoreHandlers() {
      // Desktop load more button
      document.addEventListener('click', (e) => {
        if (e.target.matches('#load-more-threads') || e.target.closest('#load-more-threads')) {
          e.preventDefault();
          const btn = e.target.closest('#load-more-threads');
          this.loadMoreThreads(btn, '#threads-list', '#load-more-container');
        }
        // Mobile load more button
        else if (e.target.matches('#mobile-load-more-threads') || e.target.closest('#mobile-load-more-threads')) {
          e.preventDefault();
          const btn = e.target.closest('#mobile-load-more-threads');
          this.loadMoreThreads(btn, '#mobile-threads-list', '#mobile-load-more-container');
        }
      });
    }

    async loadMoreThreads(button, containerSelector, buttonContainerSelector) {
      if (this.isLoading) return;

      this.isLoading = true;
      const offset = parseInt(button.dataset.offset) || 0;

      // Show loading state
      button.disabled = true;
      const icon = button.querySelector('i');
      if (icon) icon.className = 'bi bi-hourglass-split me-1';

      try {
        const response = await fetch(`${window.NovaApp.urls.loadMoreThreads}?offset=${offset}&limit=10`);
        const data = await response.json();

        if (data.html) {
          const container = document.querySelector(containerSelector);
          if (container) {
            // Merge incoming groups into existing ones instead of duplicating headers
            mergeThreadGroupsFromHtml(data.html, container);

            if (data.has_more) {
              button.dataset.offset = data.next_offset;
              button.disabled = false;
              const icon = button.querySelector('i');
              if (icon) icon.className = 'bi bi-arrow-down-circle me-1';
            } else {
              const buttonContainer = document.querySelector(buttonContainerSelector);
              if (buttonContainer) {
                // No more threads, remove the button container
                buttonContainer.remove();
              }
            }
          }
        }
      } catch (error) {
        console.error('Error loading more threads:', error);
        // Reset button state on error
        button.disabled = false;
        const icon = button.querySelector('i');
        if (icon) icon.className = 'bi bi-arrow-down-circle me-1';
      } finally {
        this.isLoading = false;
      }
    }
  }

  // ============================================================================
  // INITIALIZATION
  // ============================================================================
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      const messageManager = new MessageManager();
      messageManager.init();
      const threadLoadingManager = new ThreadLoadingManager();
      threadLoadingManager.init();
    });
  } else {
    const messageManager = new MessageManager();
    messageManager.init();
    const threadLoadingManager = new ThreadLoadingManager();
    threadLoadingManager.init();
  }

  // Expose for debugging
  window.MessageManager = MessageManager;
  window.StreamingManager = StreamingManager;
  window.MessageRenderer = MessageRenderer;
  window.ThreadLoadingManager = ThreadLoadingManager;

})();
