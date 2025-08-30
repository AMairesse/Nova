/* nova/static/js/thread_management.js - Modern chat architecture */
(function() {
  'use strict';

  // Configuration object for URLs (will be populated from template)
  window.NovaApp = window.NovaApp || {};

  // ============================================================================
  // MARKDOWN RENDERER - Unified conversion for consistency
  // ============================================================================
  class MessageRenderer {
    static markdownToHtml(text) {
      // Use marked library if available with same options as server
      if (typeof marked !== 'undefined') {
        // Configure marked with same options as server
        const renderer = new marked.Renderer();

        // Configure extensions to match server
        marked.setOptions({
          renderer: renderer,
          breaks: true, // Convert \n to <br>
          gfm: true,    // GitHub Flavored Markdown
          smartLists: true, // Better list handling
          smartypants: false, // Disable smart quotes for consistency
        });

        // Parse markdown
        let html = marked.parse(text);

        // Clean HTML with same rules as server (bleach equivalent)
        html = this.cleanHtml(html);

        return html;
      }

      // Fallback: basic HTML escaping + line breaks
      return text
        .replace(/&/g, '&')
        .replace(/</g, '<')
        .replace(/>/g, '>')
        .replace(/\n/g, '<br>');
    }

    static cleanHtml(html) {
      // Create a temporary element to parse HTML
      const tempDiv = document.createElement('div');
      tempDiv.innerHTML = html;

      // Remove disallowed tags and attributes (equivalent to bleach.clean)
      const allowedTags = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'code', 'pre', 'blockquote', 'br', 'hr', 'a'];
      const allowedAttrs = { 'a': ['href', 'title', 'rel'] };

      function cleanNode(node) {
        // Remove disallowed tags
        if (node.nodeType === Node.ELEMENT_NODE) {
          const tagName = node.tagName.toLowerCase();
          if (!allowedTags.includes(tagName)) {
            // Replace with text content
            const textNode = document.createTextNode(node.textContent);
            node.parentNode.replaceChild(textNode, node);
            return;
          }

          // Remove disallowed attributes
          const allowedAttrsForTag = allowedAttrs[tagName] || [];
          Array.from(node.attributes).forEach(attr => {
            if (!allowedAttrsForTag.includes(attr.name)) {
              node.removeAttribute(attr.name);
            }
          });
        }

        // Recursively clean child nodes
        Array.from(node.childNodes).forEach(child => cleanNode(child));
      }

      cleanNode(tempDiv);
      return tempDiv.innerHTML;
    }

    static createMessageElement(messageData) {
      const messageDiv = document.createElement('div');
      messageDiv.className = 'message mb-3';
      messageDiv.id = `message-${messageData.id}`;
      messageDiv.setAttribute('data-message-id', messageData.id);

      const html = this.markdownToHtml(messageData.text);

      if (messageData.actor === 'user' || messageData.actor === 'USR') {
        messageDiv.innerHTML = `
          <div class="card border-primary">
            <div class="card-body py-2">
              <strong class="text-primary">${html}</strong>
              ${messageData.file_count ? `<div class="mt-2 small text-muted">${messageData.file_count} file(s) attached</div>` : ''}
            </div>
          </div>
        `;
      } else if (messageData.actor === 'agent') {
        // Agent message structure
        messageDiv.innerHTML = `
          <div class="card border-secondary">
            <div class="card-body py-2">
              <div class="streaming-content">${html}</div>
            </div>
            <div class="card-footer py-1 text-muted small text-end d-none">
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

    registerStream(taskId, messageData) {
      const agentMessageEl = MessageRenderer.createMessageElement({
        ...messageData,
        actor: 'agent',
        text: '' // Start with empty content
      });

      // Add streaming class to the message container for proper CSS targeting
      agentMessageEl.classList.add('streaming');

      this.messageManager.appendMessage(agentMessageEl);

      this.activeStreams.set(taskId, {
        messageId: messageData.id,
        element: agentMessageEl,
        currentText: '',
        lastUpdate: Date.now()
      });

      // Show progress area when streaming starts
      const progressDiv = document.getElementById('task-progress');
      if (progressDiv) {
        progressDiv.classList.remove('d-none');
      }

      // Start WebSocket connection
      this.startWebSocket(taskId);
    }

    onStreamChunk(taskId, chunk) {
      const stream = this.activeStreams.get(taskId);
      if (!stream) return;

      // Skip duplicate chunks (server sometimes sends the same content multiple times)
      // Also skip empty chunks
      if (!chunk || chunk.trim() === '' || chunk === stream.lastChunk) {
        return;
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
        this.saveStreamState(taskId, stream);

        // Hide progress area
        const progressDiv = document.getElementById('task-progress');
        if (progressDiv) {
          progressDiv.classList.add('d-none');
        }

        // Hide context consumption info after a delay
        const streamingFooter = stream.element.querySelector('.card-footer');
        if (streamingFooter) {
          setTimeout(() => {
            streamingFooter.classList.add('d-none');
          }, 3000); // Hide after 3 seconds
        }
      }
      this.activeStreams.delete(taskId);
    }

    saveStreamState(taskId, stream) {
      const state = {
        messageId: stream.messageId,
        currentText: stream.currentText,
        lastUpdate: stream.lastUpdate,
        status: stream.status || 'streaming'
      };
      localStorage.setItem(`stream_${taskId}`, JSON.stringify(state));
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
          const streamingFooter = document.querySelector('.message.streaming .card-footer');
          if (streamingFooter && data.max_context) {
            streamingFooter.classList.remove('d-none');
            if (data.real_tokens !== null) {
              streamingFooter.innerHTML = `Context consumption: ${data.real_tokens}/${data.max_context} (real)`;
            } else {
              streamingFooter.innerHTML = `Context consumption: ${data.approx_tokens}/${data.max_context} (approximated)`;
            }
          }
        } else if (data.type === 'task_complete') {
          this.onStreamComplete(taskId);
        }
        // Handle other message types...
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
      this.attachEventHandlers();
      this.loadInitialThread();
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

        if (threadId) {
          localStorage.setItem('lastThreadId', threadId);
        }

        this.initTextareaFocus();
      } catch (error) {
        console.error('Error loading messages:', error);
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
        const userMessageEl = MessageRenderer.createMessageElement(data.message);
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
        // Fallback: try to find conversation container
        const conversationContainer = document.getElementById('conversation-container');
        if (conversationContainer) {
          conversationContainer.appendChild(messageElement);
        }
      }
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

    async createThread() {
      try {
        const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.createThread, { method: 'POST' });
        const data = await response.json();
        const threadList = document.querySelector('.list-group');
        if (threadList && data.threadHtml) {
          threadList.insertAdjacentHTML('afterbegin', data.threadHtml);
        }
        this.loadMessages(data.thread_id);
      } catch (error) {
        console.error('Error creating thread:', error);
      }
    }

    async deleteThread(threadId) {
      try {
        await window.DOMUtils.csrfFetch(window.NovaApp.urls.deleteThread.replace('0', threadId), { method: 'POST' });
        const threadElement = document.getElementById(`thread-item-${threadId}`);
        if (threadElement) threadElement.remove();

        // Handle file panel update for thread deletion
        const currentThreadId = localStorage.getItem('lastThreadId');
        if (currentThreadId === threadId.toString()) {
          // If we're deleting the currently active thread, handle file panel appropriately
          if (window.FileManager && typeof window.FileManager.handleThreadDeletion === 'function') {
            window.FileManager.handleThreadDeletion();
          }
        }

        const firstThread = document.querySelector('.thread-link');
        const firstThreadId = firstThread?.dataset.threadId;
        this.loadMessages(firstThreadId);
        localStorage.removeItem(`runningTasks_${threadId}`);
        if (localStorage.getItem('lastThreadId') === threadId.toString()) {
          localStorage.removeItem('lastThreadId');
        }
      } catch (error) {
        console.error('Error deleting thread:', error);
      }
    }

    loadInitialThread() {
      const lastThreadId = localStorage.getItem('lastThreadId');
      this.loadMessages(lastThreadId);
    }
  }

  // ============================================================================
  // LEGACY COMPATIBILITY - Keep existing interfaces working
  // ============================================================================
  const LegacyThreadManager = {
    init() {
      // Initialize new architecture
      const messageManager = new MessageManager();
      messageManager.init();

      // Keep legacy interface for compatibility
      this.messageManager = messageManager;
    },

    // Legacy methods that delegate to new architecture
    loadMessages(threadId) {
      return this.messageManager.loadMessages(threadId);
    },

    handleFormSubmit(form) {
      return this.messageManager.handleFormSubmit(form);
    }
  };

  // ============================================================================
  // INITIALIZATION
  // ============================================================================
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => LegacyThreadManager.init());
  } else {
    LegacyThreadManager.init();
  }

  // Expose for debugging
  window.MessageManager = MessageManager;
  window.StreamingManager = StreamingManager;
  window.MessageRenderer = MessageRenderer;

})();
