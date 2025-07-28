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

    // Auto-scroll management
    initAutoScroll();
  };

  // Auto-scroll logic
  let isAtBottom = true;
  let userScrolled = false;
  let observer = null;

  function initAutoScroll() {
    const container = $("#conversation-container");
    if (container.length === 0) {
      return;
    }

    // Disconnect previous observer if exists
    if (observer) observer.disconnect();

    // Detect if user scrolls up
    container.on("scroll", function () {
      updateIsAtBottom();
      userScrolled = !isAtBottom;
    });

    // MutationObserver to detect DOM changes (appends) and scroll
    observer = new MutationObserver(() => {
      // Force reflow
      void container[0].offsetHeight;
      updateIsAtBottom(); // Recheck after mutation
      scrollToBottomIfNeeded();
    });
    observer.observe(container[0], {
      childList: true,
      subtree: true,
      characterData: true,
    });

    // Initial scroll to bottom
    updateIsAtBottom();
    scrollToBottomIfNeeded();
  }

  function updateIsAtBottom() {
    const container = $("#conversation-container");
    const scrollTop = container.scrollTop();
    const scrollHeight = container.prop("scrollHeight");
    const height = container.height();
    isAtBottom = scrollTop + height >= scrollHeight - 1; // Tolerance reduced
  }

  function scrollToBottomIfNeeded() {
    const container = $("#conversation-container");
    if (container.length === 0) return;
    if (isAtBottom && !userScrolled) {
      const target = container.prop("scrollHeight");
      // Use rAF to set after reflow/paint
      requestAnimationFrame(() => {
        container[0].scrollTop = target;
      });
    }
  }

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
              scrollToBottomIfNeeded();
              // Start WS for task progress
              startTaskWebSocket(data.task_id);
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

  /* -------------------------- Task WebSocket for Real-Time Progress -------------------------- */
  function startTaskWebSocket(taskId) {
    if (!taskId) return;

    const progressDiv = $("#task-progress");
    const logsList = $("#progress-logs");
    const loadingSpinner = $("#progress-loading");
    const statusDiv = $("#task-status");
    progressDiv.show();
    loadingSpinner.show();

    // Determine protocol (ws or wss)
    const protocol = window.location.protocol === "https:" ? "wss" : "ws";
    const wsUrl = `${protocol}://${window.location.host}/ws/task/${taskId}/`;
    let socket = new WebSocket(wsUrl);
    let reconnectAttempts = 0;
    const maxReconnects = 5;

    socket.onopen = function () {
      console.log("WebSocket connected for task " + taskId);
      reconnectAttempts = 0; // Reset on success
    };

    socket.onmessage = function (event) {
      const data = JSON.parse(event.data);
      if (data.error) {
        statusDiv.html('<p class="text-danger">' + data.error + "</p>");
        loadingSpinner.hide();
        return;
      }

      loadingSpinner.hide();
      // Render logs with Markdown
      logsList.empty();
      data.progress_logs.forEach((log) => {
        const li = document.createElement("li");
        const logText = `${log.timestamp}: ${log.step || log.event} - ${
          log.kind || ""
        } ${log.name || ""} ${log.chunk || log.output || ""}`;
        li.innerHTML = `<small>${marked.parse(logText)}</small>`;
        logsList.append(li);
      });

      // Handle completion
      if (data.is_completed) {
        socket.close(); // Close WS
        $("#send-btn").prop("disabled", false);
        if (data.status === "COMPLETED") {
          statusDiv.html(
            '<p class="text-success">Task completed successfully.</p>'
          );
        } else {
          statusDiv.html(
            '<p class="text-danger">Task failed: ' +
              marked.parse(data.result) +
              "</p>"
          );
        }
        // Full refresh of messages and thread list
        const threadId = $('input[name="thread_id"]').val();
        $.get(window.urls.messageList, { thread_id: threadId }, (html) => {
          $("#message-container").html(html);
          initMessageContainer();
          scrollToBottomIfNeeded();
        });
        // Refresh thread list for subject updates
        $.get(window.location.href, (fullHtml) => {
          const newThreads = $(fullHtml).find(".list-group").html();
          $(".list-group").html(newThreads);
          attachThreadEventHandlers();
        });
      }
    };

    socket.onclose = function (e) {
      console.log("WebSocket closed");
      if (reconnectAttempts < maxReconnects && !e.wasClean) {
        // Reconnect if unexpected close
        reconnectAttempts++;
        setTimeout(() => {
          socket = new WebSocket(wsUrl);
        }, 1000 * reconnectAttempts); // Exponential backoff
      }
    };

    socket.onerror = function (err) {
      console.error("WebSocket error:", err);
      statusDiv.html('<p class="text-danger">WebSocket connection error.</p>');
      loadingSpinner.hide();
    };
  }

  window.startTaskWebSocket = startTaskWebSocket;
})(jQuery);
