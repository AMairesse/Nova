/* message_container.js */
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
              startAgentSSE(currentAgentId);
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

  /* -------------------------- SSE AGENT -------------------------- */
  function startAgentSSE(forcedAgentId = null) {
    const threadId = $('input[name="thread_id"]').val();
    const agentId = forcedAgentId ?? ($("#selectedAgentInput").val() || "");
    const es = new EventSource(
      `/stream-llm-response/${threadId}/?agent_id=${agentId}`
    );

    const streamState = {
      stack: [], // [{depth, el}]
      phaseStack: [],
      answerBuf: "",
      detailsVisible: false
    };

    // Helper: Create details card
    function createCard(depth, title) {
      const el = $(`
        <details class="llm-block" data-depth="${depth}">
          <summary>${title}</summary>
          <div class="stream" style="white-space:pre-wrap"></div>
        </details>
      `);
      el.css("margin-left", depth * 10 + "px");
      return el;
    }

    // Helper: Status updates
    function showStatus(text) {
      $("#agent-stream-container")
        .html(
          `<span class="spinner-border spinner-border-sm me-2" role="status"></span>${text}`
        )
        .show();
    }
    function hideStatus() {
      $("#agent-stream-container").hide().empty();
    }

    // Phase management
    function pushPhase(label) {
      streamState.phaseStack.push(label);
      showStatus(label);
    }
    function popPhase() {
      streamState.phaseStack.pop();
      if (streamState.phaseStack.length) {
        showStatus(streamState.phaseStack[streamState.phaseStack.length - 1]);
      } else {
        hideStatus();
      }
    }

    // Event mapper
    function mapEvent(ev) {
      const evt      = ev["event"];          // ex: on_chain_start
      const depth    = ev["parent_ids"]?.length ?? 0;
      const name     = ev["name"];
      const kind     = evt.startsWith("on_chain") ? "agent" : "tool";

      if (evt.endsWith("_start")) {
        return {
          "event": "start",
          "kind" : kind,
          "name" : name,
          "depth": depth
        };
      }

      if (evt.endsWith("_stream")) {
        const chunk = ev["data"]?.["chunk"] ?? "";

        function isCall(obj) {
          if (typeof obj === "string") {
            return /^\s*\[\s*{\s*"name"\s*:/.test(obj);
          }
          if (typeof obj === "object" && obj !== null) {
            return !!obj.tool_calls;
          }
          return false;
        }

        if (isCall(chunk)) return null;
        const txt = typeof chunk === "object" ? JSON.stringify(chunk) : chunk;
        if (!txt) return null;
        return {
          "event"  : "stream",
          "kind"   : kind,
          "name"   : name,
          "depth"  : depth,
          "chunk"  : txt // Raw; markdown in handler if needed
        };
      }

      if (evt.endsWith("_end")) {
        const out = ev["data"]?.["output"] ?? "";
        const txt = typeof out === "object" ? JSON.stringify(out) : out;
        return {
          "event"  : "end",
          "kind"   : kind,
          "name"   : name,
          "depth"  : depth,
          "output" : txt
        };
      }
      return null;
    }

    // Toggle details
    $("#toggle-details")
      .off("click")
      .on("click", () => {
        streamState.detailsVisible = !streamState.detailsVisible;
        $(".llm-block").toggle(streamState.detailsVisible);
        $("#toggle-details").text(
          streamState.detailsVisible ? gettext("Hide details") : gettext("Show details")
        );
      });

    showStatus(gettext("Starting agent…"));

    es.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      const mapped = mapEvent(msg);
      if (!mapped) return;

      switch (mapped.event) {
        case "start": {
          const title = `${mapped.kind} › ${mapped.name}`;
          const card = createCard(mapped.depth, title);
          streamState.stack[mapped.depth] = { depth: mapped.depth, el: card };

          if (mapped.depth === 0) {
            $("#conversation-container").append(
              `<div class="message agent"><p id="agent-answer-${threadId}"></p></div>`
            );
            streamState.answerBuf = "";
            $("#toggle-details").hide();
          }

          if (mapped.depth === 0) {
            $("#agent-stream-container").after(card);
          } else if (streamState.stack[mapped.depth - 1]) {
            streamState.stack[mapped.depth - 1].el.append(card);
          }
          if (!streamState.detailsVisible) card.hide();
          $("#toggle-details").show();

          if (mapped.depth === 0) {
            pushPhase(gettext("Agent is thinking…"));
          }
          if (mapped.kind === "tool") {
            const niceName = mapped.name || "tool";
            pushPhase(
              interpolate(
                gettext("Agent is using tool « %s »…"),
                [niceName],
                false
              )
            );
          }
          // No timeout: Observer will handle
          break;
        }

        case "stream": {
          const block = streamState.stack[mapped.depth];
          if (!block) break;
          block.el.find(".stream").append(mapped.chunk);

          if (mapped.depth === 0) {
            streamState.answerBuf += mapped.chunk;
            const p = $(`#agent-answer-${threadId}`);
            p.html(streamState.answerBuf); // Render progressive (assumes chunk is raw text/markup)
            // No timeout: Observer will handle
          }
          break;
        }

        case "end": {
          const block = streamState.stack[mapped.depth];
          if (block && mapped.output) {
            block.el.find(".stream").append(mapped.output);
          }

          popPhase();

          if (mapped.depth === 0) {
            let finalTxt = mapped.output || streamState.answerBuf;
            // Special handling for agent-tool JSON output
            try {
              const parsed = JSON.parse(finalTxt);
              if (
                parsed.agent &&
                parsed.agent.messages &&
                parsed.agent.messages[0] &&
                parsed.agent.messages[0].content
              ) {
                finalTxt = parsed.agent.messages[0].content;
              }
            } catch (e) {
              // Not JSON, keep as is
            }
            $(`#agent-answer-${threadId}`).html(finalTxt); // Set final rendered
            hideStatus();
            $("#send-btn").prop("disabled", false);
            streamState.answerBuf = ""; // Clean buffer
            // No timeout: Observer will handle
          }
          break;
        }
      }
    };

    es.onerror = () => {
      es.close();
      hideStatus();
      $("#send-btn").prop("disabled", false);
      streamState.answerBuf = ""; // Clean on error
      if (observer) observer.disconnect(); // Clean observer
    };

    es.addEventListener("close", (e) => {
      es.close();
      $("#send-btn").prop("disabled", false);
      $(`.thread-link[data-thread-id="${threadId}"]`).text(e.data);
      if (observer) observer.disconnect(); // Clean observer
    });
  }

  window.startAgentSSE = startAgentSSE;
})(jQuery);
