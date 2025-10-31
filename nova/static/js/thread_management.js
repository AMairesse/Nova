/* nova/static/js/thread_management.js */
(function () {
  'use strict';

  // Configuration object for URLs (will be populated from template)
  window.NovaApp = window.NovaApp || {};

  // ============================================================================
  // MESSAGE RENDERER
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
              <strong class="text-primary">${window.escapeHtml(messageData.text)}</strong>
              ${messageData.file_count ? `<div class="mt-2 small text-muted">${messageData.file_count} file(s) attached</div>` : ''}
            </div>
          </div>
        `;
      } else if (messageData.actor === 'agent') {
        // Remove "compact" button from previous message footer
        const container = document.getElementById('messages-list');
        if (container) {
          const selector = '.compact-thread-btn';
          const buttons = container.querySelectorAll(selector);
          const lastBtn = buttons[buttons.length - 1];
          if (lastBtn) lastBtn.remove();
        }
        // Agent message structure
        messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content">${window.escapeHtml(messageData.text)}</div>
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
                ${window.escapeHtml(messageData.text)}
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
                  <small class="text-muted streaming-content">${window.escapeHtml(messageData.internal_data.summary || '')}</small>
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
              <div class="text-muted small">${window.escapeHtml(messageData.text)}</div>
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
    }

    setMessageManager(manager) {
      this.messageManager = manager;
    }

    createMessageElement(task_id) {
      // Create agent message element with a streaming class
      const agentMessageEl = MessageRenderer.createMessageElement({
        id: task_id,
        actor: 'agent',
        text: ''
      }, this.messageManager.currentThreadId);
      agentMessageEl.classList.add('streaming');

      // Add to message manager
      this.messageManager.appendMessage(agentMessageEl);

      return agentMessageEl;
    }

    registerStream(taskId, messageData) {
      this.activeStreams.set(taskId, {
        messageId: messageData.id,
        element: '',
        status: 'streaming',
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
      const stream = this.activeStreams.get(taskId);
      if (!stream) {
        // Note: for system action (eg. "compact"), there is no activeStream
        return;
      }

      // Skip duplicate chunks (server sometimes sends the same content multiple times)
      // Also skip empty chunks
      if (!chunk || chunk.trim() === '' || chunk === stream.lastChunk) {
        return;
      }

      // Create the message element if it doesn't exist
      var messageElement = stream.element
      if (!messageElement) {
        messageElement = this.createMessageElement(taskId);
        stream.element = messageElement;
      }
      const contentEl = messageElement.querySelector('.streaming-content')

      // The server is already sending HTML chunks, so we don't need to process them as Markdown
      // Replace the entire content since server sends complete paragraph updates
      contentEl.innerHTML = chunk;

      // Track last chunk to detect duplicates
      stream.lastChunk = chunk;
    }

    onStreamComplete(taskId) {
      const stream = this.activeStreams.get(taskId);
      if (stream) {
        // Mark as completed
        stream.status = 'completed';

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

      // Mapping des handlers pour les types de messages
      const messageHandlers = {
        'pong': (data) => {
          clearTimeout(heartbeatTimeout);
        },
        'progress_update': (data) => {
          const progressLogs = document.getElementById('progress-logs');
          const log = data.progress_log || "undefined";
          if (progressLogs) progressLogs.textContent = log;
        },
        'response_chunk': (data) => {
          this.onStreamChunk(taskId, data.chunk);
        },
        'context_consumption': (data) => {
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
        },
        'new_message': (data) => {
          // Handle real-time message updates (e.g., system messages from completed tasks)
          this.onNewMessage(data.message, data.thread_id);
        },
        'task_complete': (data) => {
          // Update thread title in sidebars if backend provided it
          if (data.thread_id && data.thread_subject) {
            const links = document.querySelectorAll(`.thread-link[data-thread-id="${data.thread_id}"]`);
            links.forEach(a => {
              a.textContent = data.thread_subject;
            });
          }
          this.onStreamComplete(taskId);
        },
        'user_prompt': (data) => {
          this.onUserPrompt(taskId, data);
        },
        'interaction_update': (data) => {
          this.onInteractionUpdate(taskId, data);
        },
        'task_error': (data) => {
          this.onTaskError(taskId, data);
        }
      };

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        const handler = messageHandlers[data.type];
        if (handler) {
          handler(data);
        } else {
          console.warn('Unhandled message type:', data.type);
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
            dropdownButton.innerHTML = '<i class="bi bi-robot"></i>'; // Keep only icon
            dropdownButton.setAttribute('title', label); // Update tooltip title
            // Refresh tooltip
            const tooltipInstance = bootstrap.Tooltip.getInstance(dropdownButton);
            if (tooltipInstance) tooltipInstance.dispose();
            new bootstrap.Tooltip(dropdownButton);
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
          const payload = textarea.value;
          this.answerInteraction(interactionId, payload);
        },
        '.interaction-cancel-btn': (e, target) => {
          e.preventDefault();
          const btn = target.closest(".interaction-cancel-btn");
          const interactionId = btn.dataset.interactionId;
          this.cancelInteraction(interactionId);
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
          localStorage.removeItem('lastThreadId');
          return this.loadMessages(null);
        }

        const html = await response.text();
        document.getElementById('message-container').innerHTML = html;
        this.currentThreadId = threadId;

        document.querySelectorAll('.thread-link').forEach(a => a.classList.remove('active'));
        const active = document.querySelector(`.thread-link[data-thread-id="${this.currentThreadId}"]`);
        if (active) active.classList.add('active');

        if (threadId) {
          localStorage.setItem('lastThreadId', threadId);
        }

        this.initTextareaFocus();
        // Auto-scroll to bottom for new conversations
        this.scrollToBottom();

        // Initialize tooltips after loading
        this.initTooltips();

        // Handle server-rendered interaction cards and check for pending interactions
        this.checkPendingInteractions();
      } catch (error) {
        console.error('Error loading messages:', error);
      }
    }

    initTooltips() {
      const tooltipTriggerList = document.querySelectorAll('[data-bs-toggle="tooltip"]');
      tooltipTriggerList.forEach(tooltipTriggerEl => {
        new bootstrap.Tooltip(tooltipTriggerEl);
      });
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

    async answerInteraction(interactionId, payload) {
      const clickedBtn = document.querySelector(`.interaction-answer-btn[data-interaction-id="${interactionId}"]`);
      if (!clickedBtn || clickedBtn.disabled) return;
      const originalHtml = clickedBtn.innerHTML;
      clickedBtn.disabled = true;
      clickedBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> ' + gettext('Processing…');
      try {
        const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.interactionAnswer.replace('0', interactionId), {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload || {})
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
        const userMessageEl = MessageRenderer.createMessageElement(data.message, '');
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

    // Disable main input if there are pending interactions
    checkPendingInteractions() {
      const pendingCards = document.querySelectorAll('[data-interaction-id]');
      if (pendingCards.length > 0) {
        this.streamingManager.setInputAreaDisabled(true);
      }
    }
  }

  // ============================================================================
  // STREAMING MANAGER - Continued (add to existing class)
  // ============================================================================

  // Register background task (non-streaming operations like compact, delete)
  StreamingManager.prototype.registerBackgroundTask = function (taskId) {
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
  StreamingManager.prototype.onNewMessage = function (messageData, thread_id) {
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
    this.messageManager.scrollToBottom();
  };

  // Disable/enable the main input area while waiting for an interaction
  StreamingManager.prototype.setInputAreaDisabled = function (disabled) {
    const textarea = document.querySelector('#message-container textarea[name="new_message"]');
    const sendBtn = document.getElementById('send-btn');
    if (textarea) {
      textarea.disabled = disabled;
      textarea.placeholder = disabled ? gettext('Waiting for your answer...') : gettext('Type your message...');
    }
    if (sendBtn) {
      sendBtn.disabled = disabled;
    }
  };

  // Render and handle a user prompt card
  StreamingManager.prototype.onUserPrompt = function (taskId, data) {
    // Expected payload: { interaction_id, question, schema, origin_name, thread_id }
    const {
      interaction_id,
      question,
      schema,
      origin_name
    } = data;

    // Build card element from template
    const wrapper = document.createElement('div');
    wrapper.className = 'message mb-3';
    wrapper.id = `interaction-card-${interaction_id}`;

    const origin = origin_name ? `${window.escapeHtml(origin_name)} ${gettext('asks')}:` : gettext('Question');
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
          <div class="mb-2">${window.escapeHtml(question)}</div>
          <div class="mb-2">
            <textarea class="form-control" id="interaction-answer-input-${interaction_id}" rows="2" placeholder="${gettext('Type your answer...')}"></textarea>
            ${schemaHint}
          </div>
          <div class="d-flex gap-2">
            <button type="button" class="btn btn-sm btn-primary interaction-answer-btn" data-interaction-id="${interaction_id}">
              <i class="bi bi-check2-circle me-1"></i>${gettext('Answer')}
            </button>
            <button type="button" class="btn btn-sm btn-outline-secondary interaction-cancel-btn" data-interaction-id="${interaction_id}">
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
  };

  // Reflect backend updates to the interaction card
  StreamingManager.prototype.onInteractionUpdate = function (taskId, data) {
    const { interaction_id, interaction_status } = data;
    const card = document.getElementById(`interaction-card-${interaction_id}`);
    if (!card) return;

    const statusEl = card.querySelector('.interaction-status');
    const answerBtn = card.querySelector('.interaction-answer-btn');
    const cancelBtn = card.querySelector('.interaction-cancel-btn');
    const inputEl = card.querySelector('#interaction-answer-input-' + interaction_id);

    const disableAll = (disabled) => {
      if (answerBtn) answerBtn.disabled = disabled;
      if (cancelBtn) cancelBtn.disabled = disabled;
      if (inputEl) inputEl.disabled = disabled;
    };

    if (interaction_status === 'ANSWERED') {
      if (statusEl) statusEl.textContent = gettext('Answer received. Resuming...');
    } else if (interaction_status === 'CANCELED') {
      if (statusEl) statusEl.textContent = gettext('Canceled.');
    }
    disableAll(true);
    this.setInputAreaDisabled(false);

    // Hide card after 2 seconds
    setTimeout(() => {
      card.classList.add('d-none');
    }, 2000);
  };

  StreamingManager.prototype.onTaskError = function (taskId, error) {
    // Stop the spinner
    const spinner = document.querySelector('#task-progress .spinner-border');
    if (spinner) {
      spinner.classList.add('d-none');
    }
    // Show error message
    const progressLogs = document.getElementById('progress-logs');
    if (progressLogs) {
      progressLogs.textContent = error.message;
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

