/* nova/static/js/thread_management.js - Fusion of index.js and message_container.js */
(function() {
  'use strict';

  // Configuration object for URLs (will be populated from template)
  window.NovaApp = window.NovaApp || {};
  
  // Thread and message management functionality
  const ThreadManager = {
    init() {
      this.attachEventHandlers();
      this.loadInitialThread();
    },

    markActiveThread(threadId) {
      document.querySelectorAll('.thread-link.active').forEach(el => el.classList.remove('active'));
      if (threadId) {
        document.querySelector(`.thread-link[data-thread-id="${threadId}"]`)?.classList.add('active');
      }
    },

    attachEventHandlers() {
      // Delegated event listeners for all interactions
      document.addEventListener('click', (e) => {
        if (e.target.matches('.create-thread-btn') || e.target.closest('.create-thread-btn')) {
          e.preventDefault();
          const btn = e.target.closest('.create-thread-btn');
          this.createThread();
        } else if (e.target.matches('.thread-link') || e.target.closest('.thread-link')) {
          e.preventDefault();
          const link = e.target.closest('.thread-link');
          const threadId = link.dataset.threadId;
          this.loadMessages(threadId);
        } else if (e.target.matches('.delete-thread-btn') || e.target.closest('.delete-thread-btn')) {
          e.preventDefault();
          const btn = e.target.closest('.delete-thread-btn');
          const threadId = btn.dataset.threadId;
          this.deleteThread(threadId);
        } else if (e.target.matches('.agent-dropdown-item') || e.target.closest('.agent-dropdown-item')) {
          e.preventDefault();
          const item = e.target.closest('.agent-dropdown-item');
          const value = item.dataset.value;
          const label = item.textContent;
          const selectedAgentInput = document.getElementById('selectedAgentInput');
          const dropdownButton = document.getElementById('dropdownMenuButton');
          if (selectedAgentInput) selectedAgentInput.value = value;
          if (dropdownButton) dropdownButton.textContent = label;
        }
      });

      // Form submit
      document.addEventListener('submit', async (e) => {
        if (e.target.id === 'message-form') {
          e.preventDefault();
          await this.handleFormSubmit(e.target);
        }
      });

      // Textarea keydown and auto-resize
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
    },

    async createThread() {
      try {
        const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.createThread, { method: 'POST' });
        const data = await response.json();
        const threadList = document.querySelector('.list-group');
        if (threadList) threadList.insertAdjacentHTML('afterbegin', data.threadHtml);
        this.loadMessages(data.thread_id);
      } catch (error) {
        console.error('Error creating thread:', error);
      }
    },

    async loadMessages(threadId) {
      try {
        const params = threadId ? `?thread_id=${threadId}` : '';
        const response = await fetch(`${window.NovaApp.urls.messageList}${params}`, { headers: { 'X-AJAX': 'true' } });

        if (response.status === 404 && threadId) {
          // Thread doesn't exist - clear invalid thread ID and reload without it
          console.warn(`Thread ${threadId} not found, clearing from localStorage`);
          localStorage.removeItem('lastThreadId');
          // Retry without thread ID
          return this.loadMessages(null);
        }

        const html = await response.text();
        document.getElementById('message-container').innerHTML = html;
        this.markActiveThread(threadId);
        this.initMessageContainer();  // Internal call

        if (threadId) {
          await this.handleRunningTasks(threadId);
          localStorage.setItem('lastThreadId', threadId);
        }

        // Update file panel for the new thread
        if (window.FileManager && typeof window.FileManager.updateForThread === 'function') {
          await window.FileManager.updateForThread(threadId);
        }
      } catch (error) {
        console.error('Error loading messages:', error);
        // If there's a network error and we have a threadId, try loading without it
        if (threadId) {
          console.warn('Network error, falling back to no thread');
          localStorage.removeItem('lastThreadId');
          return this.loadMessages(null);
        }
      }
    },

    async handleRunningTasks(threadId) {
      try {
        const response = await fetch(`${window.NovaApp.urls.runningTasksBase}${threadId}/`, { headers: { 'X-AJAX': 'true' } });

        if (response.status === 404) {
          // Thread doesn't exist for running tasks - this is expected for new threads
          console.debug(`No running tasks for thread ${threadId} (thread not found)`);
          return;
        }

        const data = await response.json();
        const runningTasks = data.running_task_ids || [];
        const storedTasks = window.StorageUtils.getStoredRunningTasks(threadId);
        const tasksToResume = runningTasks.length > 0 ? runningTasks : storedTasks;

        if (tasksToResume.length > 0) {
          const progressEl = document.getElementById('task-progress');
          if (progressEl) progressEl.style.display = 'block';
          tasksToResume.forEach(taskId => this.startTaskWebSocket(threadId, taskId));
        }
      } catch (error) {
        console.error('Error fetching running tasks:', error);
      }
    },

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
    },

    loadInitialThread() {
      const lastThreadId = localStorage.getItem('lastThreadId');
      this.loadMessages(lastThreadId);
    },

    initMessageContainer() {
      const textarea = document.querySelector('#message-container textarea[name="new_message"]');
      if (textarea) textarea.focus();
    },

    async handleFormSubmit(form) {
      const textarea = form.querySelector('textarea[name="new_message"]');
      const msg = textarea ? textarea.value.trim() : '';
      if (!msg) return;

      const sendBtn = document.getElementById('send-btn');
      if (sendBtn) {
        sendBtn.disabled = true;
        sendBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> <span class="visually-hidden">Uploading...</span>';
      }

      try {
        const response = await window.DOMUtils.csrfFetch(window.NovaApp.urls.addMessage, {
          method: 'POST',
          body: new FormData(form)
        });
        const data = await response.json();
        if (data.status !== "OK") throw new Error(data.message || "Upload failed");

        const selectedAgentInput = document.getElementById('selectedAgentInput');
        const currentAgentId = selectedAgentInput ? selectedAgentInput.value : '';

        const threadIdInput = document.querySelector('input[name="thread_id"]');
        if (threadIdInput) threadIdInput.value = data.thread_id;

        if (data.threadHtml) {
          const threadList = document.querySelector('.list-group');
          if (threadList) threadList.insertAdjacentHTML('afterbegin', data.threadHtml);
        }

        const params = new URLSearchParams({ thread_id: data.thread_id, agent_id: currentAgentId });
        const messageResponse = await fetch(`${window.NovaApp.urls.messageList}?${params}`, { headers: { 'X-AJAX': 'true' } });
        const html = await messageResponse.text();
        const messageContainer = document.getElementById('message-container');
        if (messageContainer) {
          messageContainer.innerHTML = html;
          this.initMessageContainer();

          const conversationContainer = document.getElementById('conversation-container');
          if (conversationContainer) {
            const streamingDiv = document.createElement('div');
            streamingDiv.className = 'message streaming mb-3';
            streamingDiv.innerHTML = `
              <div class="card border-secondary">
                <div class="card-body py-2">
                  <div class="streaming-content"></div>
                </div>
                <div class="card-footer py-1 text-muted small text-end d-none">
                </div>
              </div>
            `;
            conversationContainer.appendChild(streamingDiv);
          }

          window.StorageUtils.addStoredTask(data.thread_id, data.task_id);
          this.startTaskWebSocket(data.thread_id, data.task_id);
        }

        if (sendBtn) sendBtn.innerHTML = '<i class="bi bi-send-fill"></i> <span class="visually-hidden">Send</span>';
      } catch (error) {
        console.error("Error adding message:", error);
        if (sendBtn) {
          sendBtn.disabled = false;
          sendBtn.innerHTML = '<i class="bi bi-send-fill"></i> <span class="visually-hidden">Send</span>';
        }
      }
    },

    startTaskWebSocket(threadId, taskId) {
      if (!taskId) return;

      const progressDiv = document.getElementById('task-progress');
      const progressLogs = document.getElementById('progress-logs');
      const statusDiv = document.getElementById('task-status');
      if (progressDiv) progressDiv.classList.remove('d-none');

      const protocol = window.location.protocol === "https:" ? "wss" : "ws";
      const wsUrl = `${protocol}://${window.location.host}/ws/task/${taskId}/`;
      let socket = new WebSocket(wsUrl);
      let reconnectAttempts = 0;
      const maxReconnects = 5;

      let heartbeatInterval = null;
      let heartbeatTimeout = null;

      function startHeartbeat() {
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
      }

      function handlePong() {
        clearTimeout(heartbeatTimeout);
      }

      socket.onopen = () => {
        reconnectAttempts = 0;
        startHeartbeat();
      };

      socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        if (data.type === 'pong') {
          handlePong();
          return;
        }
        if (data.error) {
          if (statusDiv) statusDiv.innerHTML = '<p class="text-danger">' + data.error + "</p>";
          return;
        }

        if (data.type === 'progress_update') {
          const log = data.progress_log || "undefined";
          if (progressLogs) progressLogs.textContent = log;
          return;
        }

        if (data.type === 'response_chunk') {
          if (progressDiv && !progressDiv.classList.contains('d-none') && data.chunk !== '') {
            progressDiv.classList.add('d-none');
          }
          const streamingContent = document.querySelector(".message.streaming .streaming-content");
          if (streamingContent) streamingContent.innerHTML = data.chunk;
          return;
        }

        if (data.type === 'context_consumption') {
          const streamingFooter = document.querySelector(".message.streaming .card-footer");
          if (streamingFooter && data.max_context) {
            if (data.real_tokens !== null) {
              streamingFooter.classList.remove('d-none');
              streamingFooter.innerHTML = `Context consumption: ${data.real_tokens}/${data.max_context} (real)`;
            }
            else {
              streamingFooter.classList.remove('d-none');
              streamingFooter.innerHTML = `Context consumption: ${data.approx_tokens}/${data.max_context} (approximated)`;
            }
          }
          return;
        }

        if (data.type === 'task_complete') {
          const sendBtn = document.getElementById('send-btn');
          if (sendBtn) sendBtn.disabled = false;
          socket.close();
          const timestamp = Date.now();
          fetch(`${window.location.href}?t=${timestamp}`)
            .then(response => response.text())
            .then(fullHtml => {
              const parser = new DOMParser();
              const doc = parser.parseFromString(fullHtml, 'text/html');
              const newThreadsHtml = doc.querySelector('.list-group');
              const currentThreadList = document.querySelector('.list-group');
              if (newThreadsHtml && currentThreadList) currentThreadList.innerHTML = newThreadsHtml.innerHTML;
            })
            .catch(error => console.error('Error refreshing thread list:', error));
          window.StorageUtils.removeStoredTask(threadId, taskId);
          return;
        }
      };

      socket.onclose = (e) => {
        clearInterval(heartbeatInterval);
        clearTimeout(heartbeatTimeout);
        if (reconnectAttempts < maxReconnects && !e.wasClean) {
          reconnectAttempts++;
          setTimeout(() => {
            socket = new WebSocket(wsUrl);
            // Reassign handlers...
          }, 1000 * reconnectAttempts);
        }
      };

      socket.onerror = (err) => {
        if (statusDiv) statusDiv.innerHTML = '<p class="text-danger">WebSocket connection error.</p>';
      };
    }
  };

  // Initialize when DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ThreadManager.init());
  } else {
    ThreadManager.init();
  }

  // Scroll to bottom functionality
  const ScrollManager = {
    init() {
      this.attachScrollHandlers();
      this.initScrollToBottomButton();
    },

    attachScrollHandlers() {
      // Monitor scroll in conversation container to show/hide scroll button
      document.addEventListener('scroll', this.handleScroll.bind(this), true);
    },

    handleScroll(e) {
      if (e.target.id === 'conversation-container') {
        const scrollButton = document.getElementById('scroll-to-bottom');
        if (!scrollButton) return;

        const container = e.target;
        const isNearBottom = container.scrollTop + container.clientHeight >= container.scrollHeight - 100;
        
        if (isNearBottom) {
          scrollButton.classList.add('d-none');
        } else {
          scrollButton.classList.remove('d-none');
        }
      }
    },

    initScrollToBottomButton() {
      document.addEventListener('click', (e) => {
        if (e.target.matches('#scroll-to-bottom') || e.target.closest('#scroll-to-bottom')) {
          e.preventDefault();
          this.scrollToBottom();
        }
      });
    },

    scrollToBottom() {
      const conversationContainer = document.getElementById('conversation-container');
      if (conversationContainer) {
        conversationContainer.scrollTo({
          top: conversationContainer.scrollHeight,
          behavior: 'smooth'
        });
      }
    }
  };

  // Files toggle functionality
  const FilesToggleManager = {
    init() {
      this.attachToggleHandler();
      // Initialize files panel on page load
      this.initializeFilesPanel();
    },

    attachToggleHandler() {
      document.addEventListener('click', (e) => {
        if (e.target.matches('#files-toggle-btn') || e.target.closest('#files-toggle-btn')) {
          e.preventDefault();
          window.FileManager.toggleSidebar();
        }
      });
    },

    async initializeFilesPanel() {
      // Auto-load files panel content when page loads
      const filesColumn = document.getElementById('files-column');
      if (filesColumn && !filesColumn.classList.contains('d-none')) {
        // Panel is visible by default, initialize it
        setTimeout(async () => {
          if (window.FileManager && typeof window.FileManager.toggleSidebar === 'function') {
            // Just load content without toggling visibility
            const currentThreadId = localStorage.getItem('lastThreadId');
            if (currentThreadId) {
              window.FileManager.currentThreadId = currentThreadId;
              
              const contentEl = document.getElementById('file-sidebar-content');
              if (contentEl && !window.FileManager.sidebarContentLoaded) {
                try {
                  const response = await fetch('/files/sidebar-panel/');
                  if (response.ok) {
                    const html = await response.text();
                    contentEl.innerHTML = html;
                    window.FileManager.attachSidebarEventHandlers();
                    window.FileManager.sidebarContentLoaded = true;
                    await window.FileManager.loadTree();
                    window.FileManager.connectWebSocket();
                  }
                } catch (error) {
                  console.error('Error initializing files panel:', error);
                }
              }
            }
          }
        }, 500);
      }
    }
  };

  // Initialize all managers
  ScrollManager.init();
  FilesToggleManager.init();
})();
