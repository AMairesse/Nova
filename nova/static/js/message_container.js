/* nova/static/js/message_container.js */
(function ($) {
  /* ----------- Public function called after each injection ----------- */
  window.initMessageContainer = function () {
    const textarea = $("#message-container").find(
      'textarea[name="new_message"]'
    );
    textarea.focus();

    // Manage the Enter key
    textarea.on("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        $("#message-form").submit();
      }
    });

    // Auto-resize
    textarea.on("input", function () {
      this.style.height = "38px";
      this.style.height = this.scrollHeight + "px";
    });
  };

  // Form submit (add trim + empty check)
  $(document).on("submit", "#message-form", function (e) {
    e.preventDefault();
    const msg = $('textarea[name="new_message"]').val().trim();
    if (!msg) return; // Prevent empty
    $("#send-btn").prop("disabled", true);

    const formData = $(this).serialize();

    getCSRFToken().then((token) =>
      $.ajax({
        type: "POST",
        url: window.urls.addMessage,
        data: formData,
        headers: { "X-AJAX": "true", "X-CSRFToken": token },

        success: function (data) {
          // 1) Memorize selected agent BEFORE updating the DOM
          const currentAgentId = $("#selectedAgentInput").val() || "";

          // Update or create the thread
          $('input[name="thread_id"]').val(data.thread_id);

          // If we get HTML for a new thread then we add it
          if (data.threadHtml) {
            $(".list-group").prepend(data.threadHtml);
            attachThreadEventHandlers();
          }

          // Thread's messages reload
          $.ajax({
            type: "GET",
            url: window.urls.messageList,
            data: { thread_id: data.thread_id, agent_id: currentAgentId },
            headers: { "X-AJAX": "true" },

            success: function (html) {
              $("#message-container").html(html);
              window.initMessageContainer();

              // Create streaming placeholder
              const streamingDiv = $('<div class="message streaming"><p></p></div>');
              $("#conversation-container").append(streamingDiv);

              // Step 3: Store task_id in localStorage for persistence
              window.addStoredTask(data.thread_id, data.task_id);

              // Start WS for task progress (pass threadId for cleanup)
              startTaskWebSocket(data.thread_id, data.task_id);
            },
          });
        },

        error: function (_, __, err) {
          console.error("Error adding message:", err);
          $("#send-btn").prop("disabled", false);
        },
      })
    );
  });

  // Select an agent in the dropdown
  $(document).on("click", ".dropdown-item", function (e) {
    e.preventDefault();
    const value = $(this).data("value");
    const label = $(this).text();

    $("#selectedAgentInput").val(value);
    $("#dropdownMenuButton").text(label);
  });

  /* -------------------------- LocalStorage with Expiration -------------------------- */
  // Helper to set item with expiry (TTL in ms, default 1h)
  function setWithExpiry(key, value, ttl = 3600000) {
    const now = new Date().getTime();
    const item = { value: value, expiry: now + ttl };
    localStorage.setItem(key, JSON.stringify(item));
  }

  // Helper to get item and check expiry
  function getWithExpiry(key) {
    const itemStr = localStorage.getItem(key);
    if (!itemStr) return null;
    const item = JSON.parse(itemStr);
    const now = new Date().getTime();
    if (now > item.expiry) {
      localStorage.removeItem(key);
      return null;
    }
    return item.value;
  }

  // Store task_id for a thread_id (using expiry)
  window.addStoredTask = function(threadId, taskId) {
    if (!threadId || !taskId) return;
    const key = `storedTask_${threadId}`;
    setWithExpiry(key, taskId);
  };

  // Remove stored task and clean if expired
  window.removeStoredTask = function(threadId, taskId) {
    const key = `storedTask_${threadId}`;
    const storedTask = getWithExpiry(key); // Auto-cleans if expired
    if (storedTask === taskId) {
      localStorage.removeItem(key);
    }
  };

  /* -------------------------- Task WebSocket for Real-Time Progress -------------------------- */
  function startTaskWebSocket(threadId, taskId) {
    if (!taskId) return;

    const progressDiv = $("#task-progress");
    const logsList = $("#progress-logs");
    const statusDiv = $("#task-status");
    progressDiv.show();

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
            socket.close(1006, 'Heartbeat timeout'); // Abnormal closure
          }, 10000); // 10s timeout
        }
      }, 30000); // Every 30s
    }

    function handlePong() {
      clearTimeout(heartbeatTimeout);
    }

    socket.onopen = function () {
      reconnectAttempts = 0; // Reset on success
      startHeartbeat(); // Start heartbeat
    };

    socket.onmessage = function (event) {
      const data = JSON.parse(event.data);
      if (data.type === 'pong') {
        handlePong();
        return;
      }
      if (data.error) {
        statusDiv.html('<p class="text-danger">' + data.error + "</p>");
        return;
      }

      if (data.type === 'progress_update') {
        const log = data.progress_log || "undefined";
        const li = document.createElement("li");
        li.innerHTML = `<small>${marked.parse(log)}</small>`;
        logsList.append(li);
        return;
      }

      if (data.type === 'response_chunk') {
        // Set full parsed HTML (replaces content each time)
        const streamingP = $(".message.streaming p");
        streamingP.html(data.chunk);
        return;
      }

      if (data.type === 'task_complete') {
        // Activate send button
        $("#send-btn").prop("disabled", false);
        // If only the answer is done the hide the progressDiv
        if (data.status === "RESPONSE_COMPLETED") {
          progressDiv.hide();
        }
        // If the task is fully completed
        else if (data.status === "TASK_COMPLETED") {
          // Close WS
          socket.close();
          // Refresh thread list for subject updates with no-cache
          const timestamp = Date.now();
          $.get(`${window.location.href}?t=${timestamp}`, (fullHtml) => {
            const newThreads = $(fullHtml).find(".list-group").html();
            $(".list-group").html(newThreads);
            attachThreadEventHandlers();
          });
        }
        // If the task failed
        else {
          statusDiv.html('<p class="text-danger">Task failed: ' + marked.parse(data.result) + "</p>");
        }
        // Clean stored task
        window.removeStoredTask(threadId, taskId);
        return;
      }
    };

    socket.onclose = function (e) {
      clearInterval(heartbeatInterval);
      clearTimeout(heartbeatTimeout);
      if (reconnectAttempts < maxReconnects && !e.wasClean) {
        // Reconnect if unexpected close
        reconnectAttempts++;
        setTimeout(() => {
          socket = new WebSocket(wsUrl);
          // Re-attach event handlers here if needed
          socket.onopen = this.onopen; // Reuse handlers
          socket.onmessage = this.onmessage;
          socket.onclose = this.onclose;
          socket.onerror = this.onerror;
        }, 1000 * reconnectAttempts); // Exponential backoff
      }
    };

    socket.onerror = function (err) {
      statusDiv.html('<p class="text-danger">WebSocket connection error.</p>');
    };
  }

  window.startTaskWebSocket = startTaskWebSocket;
})(jQuery);
