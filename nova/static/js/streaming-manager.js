// static/nova/js/streaming-manager.js
(function () {
    'use strict';

    // ============================================================================
    // STREAMING MANAGER - Coordinates WebSocket and message streaming
    // ============================================================================
    window.StreamingManager = class StreamingManager {
        constructor() {
            this.activeStreams = new Map(); // taskId -> stream data
            this.messageManager = null;
        }

        setMessageManager(manager) {
            this.messageManager = manager;
        }

        createMessageElement(task_id) {
            // Create agent message element with a streaming class
            const agentMessageEl = window.MessageRenderer.createMessageElement({
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

            // Disable input area while agent is working
            this.setInputAreaDisabled(true);

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

            // Create the message element if it doesn't exist (including on reconnect)
            if (!stream.element) {
                stream.element = this.createMessageElement(taskId);
                stream.status = 'streaming';
            }
            const contentEl = stream.element.querySelector('.streaming-content');

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

                // Re-enable input area when task completes
                this.setInputAreaDisabled(false);
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

                // Announce webapp update; actual debounced refresh handled by PreviewManager
                'webapp_update': (data) => {
                    try {
                        const slug = data.slug || '';
                        document.dispatchEvent(new CustomEvent('webapp_update', { detail: { slug } }));
                    } catch (e) {
                        console.warn('webapp_update handler error:', e);
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

                // Receive initial public URL and announce it to the page (index.html script listens)
                'webapp_public_url': (data) => {
                    try {
                        const slug = data.slug || '';
                        const public_url = data.public_url || '';
                        if (!public_url) return;
                        document.dispatchEvent(new CustomEvent('webapp_public_url', { detail: { slug, public_url } }));
                    } catch (e) {
                        console.warn('webapp_public_url handler error:', e);
                    }
                },

                'task_error': (data) => {
                    this.onTaskError(taskId, data);
                },
                'summarization_complete': (data) => {
                    this.onSummarizationComplete(data);
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

        // Register background task (non-streaming operations like compact, delete)
        registerBackgroundTask(taskId) {
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
        }

        // Reconnect to an existing task (when user returns to page)
        reconnectToTask(taskId, currentResponse, lastProgress) {
            // Check if already connected
            if (this.activeStreams.has(taskId)) {
                return;
            }

            // Register the stream with reconnect flag
            this.activeStreams.set(taskId, {
                messageId: taskId,
                element: null,
                status: 'reconnecting',
                isReconnect: true,
                lastChunk: currentResponse || ''
            });

            // Show progress area
            const progressDiv = document.getElementById('task-progress');
            if (progressDiv) {
                progressDiv.classList.remove('d-none');
                const spinner = progressDiv.querySelector('.spinner-border');
                if (spinner) {
                    spinner.classList.remove('d-none');
                }
            }

            // Set last known progress message
            const progressLogs = document.getElementById('progress-logs');
            if (progressLogs && lastProgress) {
                progressLogs.textContent = lastProgress.step || 'Reconnecting...';
            }

            // Disable input area while task is running
            this.setInputAreaDisabled(true);

            // If we have current response, show it immediately
            if (currentResponse) {
                const stream = this.activeStreams.get(taskId);
                stream.element = this.createMessageElement(taskId);
                const contentEl = stream.element.querySelector('.streaming-content');
                contentEl.innerHTML = currentResponse;
            }

            // Start WebSocket connection for live updates
            this.startWebSocket(taskId);
        }

        // Handle real-time message updates like system messages
        onNewMessage(messageData, thread_id) {
            // Create message element for the new message
            const messageElement = window.MessageRenderer.createMessageElement(messageData, thread_id);

            // Add to message container
            const messagesList = document.getElementById('messages-list');
            if (messagesList) {
                messagesList.appendChild(messageElement);
            } else {
                console.error('Messages list not found for new message');
            }

            // Update compact link visibility after adding new message
            if (this.messageManager) {
                this.messageManager.updateCompactLinkVisibility();
            }

            // Scroll to bottom to show new message
            this.messageManager.scrollToBottom();
        }

        // Disable/enable the main input area while waiting for an interaction
        setInputAreaDisabled(disabled) {
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            const sendBtn = document.getElementById('send-btn');
            if (textarea) {
                textarea.disabled = disabled;
                textarea.placeholder = disabled ? gettext('Waiting for your answer...') : gettext('Type your message...');
            }
            if (sendBtn) {
                sendBtn.disabled = disabled;
            }
        }

        // Render and handle a user prompt card
        onUserPrompt(taskId, data) {
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

            const origin = origin_name ? `${window.DOMUtils.escapeHTML(origin_name)} ${gettext('asks')}:` : gettext('Question');
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
            <div class="mb-2">${window.DOMUtils.escapeHTML(question)}</div>
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
        }

        // Reflect backend updates to the interaction card
        onInteractionUpdate(taskId, data) {
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
        }

        onTaskError(taskId, error) {
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
            // Re-enable input area on error
            this.setInputAreaDisabled(false);
        }

        onSummarizationComplete(data) {
            // Show summarization notification
            const { summary, original_tokens, summary_tokens, strategy } = data;

            // Create a toast notification or system message
            const notification = document.createElement('div');
            notification.className = 'alert alert-info alert-dismissible fade show position-fixed';
            notification.style.cssText = 'top: 20px; right: 20px; z-index: 9999; max-width: 400px;';
            notification.innerHTML = `
                <i class="bi bi-info-circle me-2"></i>
                <strong>Conversation summarized</strong><br>
                <small>Reduced from ${original_tokens} to ~${summary_tokens} tokens using ${strategy} strategy</small>
                <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
            `;

            document.body.appendChild(notification);

            // Auto-remove after 5 seconds
            setTimeout(() => {
                if (notification.parentNode) {
                    notification.remove();
                }
            }, 5000);

            // Update progress logs
            const progressLogs = document.getElementById('progress-logs');
            if (progressLogs) {
                progressLogs.textContent = `Conversation summarized (${original_tokens} → ${summary_tokens} tokens)`;
            }

            // Change compact link text to "Compaction done"
            const compactLinks = document.querySelectorAll('.compact-thread-link');
            compactLinks.forEach(link => {
                link.innerHTML = '<i class="bi bi-check-circle me-1"></i>' + gettext('Compaction done');
                link.style.pointerEvents = 'none';
                link.style.opacity = '0.6';
                link.classList.add('text-success');
            });

            // Find the task ID from active streams and complete it (re-enables input)
            // Since we don't know which task this is for, complete all active streams
            // This is a bit of a hack, but works for the current use case
            for (const [taskId, stream] of this.activeStreams) {
                if (stream.status === 'streaming') {
                    this.onStreamComplete(taskId);
                    break; // Only complete one stream (the summarization task)
                }
            }
        }
    };

})();