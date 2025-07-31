/* nova/static/js/message_container.js */
(function () {
  'use strict';

  /* ----------- Public function called after each injection ----------- */
  window.initMessageContainer = function () {
    const textarea = document.querySelector('#message-container textarea[name="new_message"]');
    if (textarea) {
      textarea.focus();

      // Manage the Enter key
      textarea.addEventListener('keydown', function (e) {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          const form = document.getElementById('message-form');
          if (form) {
            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
          }
        }
      });

      // Auto-resize for textareas with the auto-resize class
      if (textarea.classList.contains('auto-resize-textarea')) {
        textarea.addEventListener('input', function () {
          this.style.height = "38px";
          this.style.height = this.scrollHeight + "px";
        });
      }
    }
  };

  // Form submit (add trim + empty check)
  document.addEventListener('submit', async function (e) {
    if (e.target.id !== 'message-form') return;
    
    e.preventDefault();
    const textarea = document.querySelector('textarea[name="new_message"]');
    const msg = textarea ? textarea.value.trim() : '';
    if (!msg) return; // Prevent empty
    
    const sendBtn = document.getElementById('send-btn');
    if (sendBtn) sendBtn.disabled = true;

    try {
      const token = await window.getCSRFToken();
      const formData = new FormData(e.target);
      
      const response = await fetch(window.NovaApp.urls.addMessage, {
        method: 'POST',
        body: formData,
        headers: {
          'X-AJAX': 'true',
          'X-CSRFToken': token
        }
      });

      const data = await response.json();
      
      // 1) Memorize selected agent BEFORE updating the DOM
      const selectedAgentInput = document.getElementById('selectedAgentInput');
      const currentAgentId = selectedAgentInput ? selectedAgentInput.value : '';

      // Update or create the thread
      const threadIdInput = document.querySelector('input[name="thread_id"]');
      if (threadIdInput) threadIdInput.value = data.thread_id;

      // If we get HTML for a new thread then we add it
      if (data.threadHtml) {
        const threadList = document.querySelector('.list-group');
        if (threadList) {
          threadList.insertAdjacentHTML('afterbegin', data.threadHtml);
        }
      }

      // Thread's messages reload
      const params = new URLSearchParams({
        thread_id: data.thread_id,
        agent_id: currentAgentId
      });
      
      const messageResponse = await fetch(`${window.NovaApp.urls.messageList}?${params}`, {
        headers: { 'X-AJAX': 'true' }
      });
      
      const html = await messageResponse.text();
      const messageContainer = document.getElementById('message-container');
      if (messageContainer) {
        messageContainer.innerHTML = html;
        window.initMessageContainer();

        // Create streaming placeholder with card layout
        const conversationContainer = document.getElementById('conversation-container');
        if (conversationContainer) {
          const streamingDiv = document.createElement('div');
          streamingDiv.className = 'message streaming mb-3';
          streamingDiv.innerHTML = `
            <div class="card border-secondary">
              <div class="card-body py-2">
                <div class="streaming-content"></div>
              </div>
            </div>
          `;
          conversationContainer.appendChild(streamingDiv);
        }

        // Store task_id in localStorage for persistence
        window.StorageUtils.addStoredTask(data.thread_id, data.task_id);

        // Start WS for task progress
        startTaskWebSocket(data.thread_id, data.task_id);
      }
    } catch (error) {
      console.error("Error adding message:", error);
      if (sendBtn) sendBtn.disabled = false;
    }
  });

  // Select an agent in the dropdown (only for agent dropdown items)
  document.addEventListener('click', function (e) {
    if (e.target.matches('.agent-dropdown-item') || e.target.closest('.agent-dropdown-item')) {
      e.preventDefault();
      const item = e.target.matches('.agent-dropdown-item') ? e.target : e.target.closest('.agent-dropdown-item');
      const value = item.dataset.value;
      const label = item.textContent;

      const selectedAgentInput = document.getElementById('selectedAgentInput');
      const dropdownButton = document.getElementById('dropdownMenuButton');
      
      if (selectedAgentInput) selectedAgentInput.value = value;
      if (dropdownButton) dropdownButton.textContent = label;
    }
  });

  // Note: localStorage helpers are now in utils.js - keeping backward compatibility
  window.addStoredTask = window.StorageUtils.addStoredTask.bind(window.StorageUtils);
  window.removeStoredTask = window.StorageUtils.removeStoredTask.bind(window.StorageUtils);

  /* -------------------------- Task WebSocket for Real-Time Progress -------------------------- */
  function startTaskWebSocket(threadId, taskId) {
    if (!taskId) return;

    const progressDiv = document.getElementById('task-progress');
    const progressLogs = document.getElementById('progress-logs');
    const statusDiv = document.getElementById('task-status');
    
    if (progressDiv) progressDiv.classList.remove('d-none');

    // Determine protocol (ws or wss)
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${protocol}://${window.location.host}/ws/task/${taskId}/`;
    let socket = new WebSocket(wsUrl);
    let reconnectAttempts = 0;
    const maxReconnects = 5;

    // Heartbeat variables
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

    socket.onopen = function () {
      reconnectAttempts = 0;
      startHeartbeat();
    };

    socket.onmessage = function (event) {
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
        // When the streaming starts remove the progressDiv
        if (progressDiv && !progressDiv.classList.contains('d-none') && data.chunk !== '') {
          progressDiv.classList.add('d-none');
        }
        // Set full parsed HTML (replaces content each time)
        const streamingContent = document.querySelector(".message.streaming .streaming-content");
        if (streamingContent) streamingContent.innerHTML = data.chunk;
        return;
      }

      if (data.type === 'task_complete') {
        // Activate send button
        const sendBtn = document.getElementById('send-btn');
        if (sendBtn) sendBtn.disabled = false;
        
        // Close WS
        socket.close();
        
        // Refresh thread list for subject updates with no-cache
        const timestamp = Date.now();
        fetch(`${window.location.href}?t=${timestamp}`)
          .then(response => response.text())
          .then(fullHtml => {
            const parser = new DOMParser();
            const doc = parser.parseFromString(fullHtml, 'text/html');
            const newThreadsHtml = doc.querySelector('.list-group');
            const currentThreadList = document.querySelector('.list-group');
            if (newThreadsHtml && currentThreadList) {
              currentThreadList.innerHTML = newThreadsHtml.innerHTML;
            }
          })
          .catch(error => console.error('Error refreshing thread list:', error));
        
        // Clean stored task
        window.StorageUtils.removeStoredTask(threadId, taskId);
        return;
      }
    };

    socket.onclose = function (e) {
      clearInterval(heartbeatInterval);
      clearTimeout(heartbeatTimeout);
      if (reconnectAttempts < maxReconnects && !e.wasClean) {
        reconnectAttempts++;
        setTimeout(() => {
          socket = new WebSocket(wsUrl);
          socket.onopen = this.onopen;
          socket.onmessage = this.onmessage;
          socket.onclose = this.onclose;
          socket.onerror = this.onerror;
        }, 1000 * reconnectAttempts);
      }
    };

    socket.onerror = function (err) {
      if (statusDiv) statusDiv.innerHTML = '<p class="text-danger">WebSocket connection error.</p>';
    };
  }

  window.startTaskWebSocket = startTaskWebSocket;
})();
