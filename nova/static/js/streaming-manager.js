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
            this.maxReconnectAttempts = 6;
            this.reconnectDelays = [500, 1000, 2000, 4000, 8000, 15000];
            this.statePollMs = 15000;
            this._lifecycleBound = false;
        }

        setMessageManager(manager) {
            this.messageManager = manager;
            if (this._lifecycleBound) return;
            this._lifecycleBound = true;
            const refresh = () => {
                for (const stream of this.activeStreams.values()) {
                    if (stream.status === 'running' || stream.status === 'streaming' || stream.status === 'reconnecting') {
                        this.fetchTaskState(stream.taskId);
                    }
                }
            };
            document.addEventListener('visibilitychange', () => {
                if (document.visibilityState === 'visible') refresh();
            });
            window.addEventListener('focus', refresh);
            document.addEventListener('threadChanged', (event) => this.onThreadChanged(event.detail?.threadId));
        }

        _currentThreadId() {
            return this.messageManager?.currentThreadId ? String(this.messageManager.currentThreadId) : '';
        }

        _ownsProgress(taskId) {
            const displayed = document.getElementById('task-progress-trace-btn')?.dataset.taskId;
            return !displayed || displayed === String(taskId);
        }

        _belongsToCurrentThread(stream, threadId) {
            const eventThreadId = threadId === null || threadId === undefined ? '' : String(threadId);
            return Boolean(stream && stream.threadId && stream.threadId === this._currentThreadId()
                && (!eventThreadId || eventThreadId === stream.threadId));
        }

        _stateUrl(taskId) {
            const base = window.NovaApp?.urls?.taskStateBase || '/tasks/';
            return `${base.replace(/\/$/, '')}/${encodeURIComponent(taskId)}/state/`;
        }

        _clearTimer(stream, key) {
            if (stream?.[key]) {
                window.clearTimeout(stream[key]);
                stream[key] = null;
            }
        }

        _cleanupStream(taskId, { terminal = false } = {}) {
            const stream = this.activeStreams.get(String(taskId));
            if (!stream) return;
            this._clearTimer(stream, 'reconnectTimer');
            this._clearTimer(stream, 'pollTimer');
            this._clearTimer(stream, 'heartbeatTimeout');
            if (stream.heartbeatInterval) window.clearInterval(stream.heartbeatInterval);
            const socket = stream.socket;
            stream.socket = null;
            if (socket && socket.readyState !== WebSocket.CLOSED) socket.close();
            if (terminal) this.activeStreams.delete(String(taskId));
        }

        onThreadChanged(threadId) {
            const selected = threadId === null || threadId === undefined ? '' : String(threadId);
            for (const [taskId, stream] of this.activeStreams) {
                if (stream.threadId !== selected) {
                    this._cleanupStream(taskId, { terminal: true });
                } else {
                    if (stream.element && !stream.element.isConnected) {
                        stream.element = null;
                        stream.lastChunk = '';
                        stream.hasLiveContent = false;
                        stream.finalMessageReceived = false;
                    }
                    this.setInputAreaDisabled(!stream.terminalEvent);
                    this.fetchTaskState(taskId);
                }
            }
        }

        updateThreadSubject(threadId, threadSubject) {
            if (!threadId || !threadSubject) return;
            const links = document.querySelectorAll(`.thread-link[data-thread-id="${threadId}"]`);
            links.forEach(a => {
                a.textContent = threadSubject;
            });
        }

        setExecutionTraceButton(taskId, visible = true) {
            const button = document.getElementById('task-progress-trace-btn');
            if (!button) {
                return;
            }
            button.dataset.taskId = visible ? String(taskId || '') : '';
            button.classList.toggle('d-none', !visible || !taskId);
        }

        createMessageElement(task_id) {
            // Create agent message element with a streaming class
            const agentMessageEl = window.MessageRenderer.createMessageElement({
                id: `stream-${task_id}`,
                actor: 'agent',
                text: ''
            }, this.messageManager.currentThreadId);
            agentMessageEl.classList.add('streaming');

            // Add to message manager
            this.messageManager.appendMessage(agentMessageEl);

            return agentMessageEl;
        }

        registerStream(taskId, messageData) {
            const normalizedTaskId = String(taskId);
            this._cleanupStream(normalizedTaskId, { terminal: true });
            this.activeStreams.set(normalizedTaskId, {
                taskId: normalizedTaskId,
                messageId: messageData.id,
                element: '',
                placeholderActive: false,
                status: 'running',
                threadId: String(messageData.thread_id || this._currentThreadId()),
                revision: 0,
                updatedAt: 0,
                localEventCounter: 0,
                reconnectAttempts: 0,
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
            this.setExecutionTraceButton(taskId, true);

            // Disable input area while agent is working
            this.setInputAreaDisabled(true);

            // Start WebSocket connection
            this.startWebSocket(normalizedTaskId);
            this.fetchTaskState(normalizedTaskId);
        }

        onStreamChunk(taskId, chunk) {
            const stream = this.activeStreams.get(String(taskId));
            if (!stream || stream.finalMessageReceived || !this._belongsToCurrentThread(stream)) {
                // Note: for system action (eg. "compact"), there is no activeStream
                return;
            }

            // Skip duplicate chunks (server sometimes sends the same content multiple times)
            // Also skip empty chunks
            if (typeof chunk !== 'string' || !chunk.trim() || chunk === stream.lastChunk) {
                return;
            }

            // Create the message element if it doesn't exist (including on reconnect)
            if (!stream.element) {
                stream.element = this.createMessageElement(taskId);
                stream.placeholderActive = true;
                stream.status = 'streaming';
            }
            const contentEl = stream.element.querySelector('.streaming-content');
            if (!contentEl) return;

            // The server is already sending HTML chunks, so we don't need to process them as Markdown
            // Replace the entire content since server sends complete paragraph updates
            contentEl.innerHTML = chunk;
            this.messageManager?.followBottomDuringLayout?.({
                force: false,
                behavior: 'auto',
                observeRoot: stream.element,
            });

            // Track last chunk to detect duplicates
            stream.lastChunk = chunk;
            stream.localEventCounter += 1;
        }

        onStreamComplete(taskId) {
            const normalizedTaskId = String(taskId);
            const stream = this.activeStreams.get(normalizedTaskId);
            if (stream && this._belongsToCurrentThread(stream) && this._ownsProgress(taskId)) {
                // Mark as completed
                stream.status = 'completed';
                this._cleanupStream(normalizedTaskId);

                // Immediately hide the spinner when task completes
                const spinner = document.querySelector('#task-progress .spinner-border');
                if (spinner) {
                    spinner.classList.add('d-none');
                }

                // Hide entire progress area after a delay
                const progressDiv = document.getElementById('task-progress');
                if (progressDiv) {
                    setTimeout(() => {
                        if (!this.activeStreams.size || document.getElementById('task-progress-trace-btn')?.dataset.taskId === normalizedTaskId) {
                            progressDiv.classList.add('d-none');
                            if (!this.activeStreams.size) this.setExecutionTraceButton('', false);
                        }
                    }, 3000); // Hide progress after 3 seconds
                }

                // Re-enable input area when task completes
                this.setInputAreaDisabled(false);
            }
            this._cleanupStream(normalizedTaskId, { terminal: true });
        }

        reconcileTerminalEvent(taskId, error = null) {
            const stream = this.activeStreams.get(String(taskId));
            if (!stream || !this._belongsToCurrentThread(stream)) return;
            stream.terminalEvent = true;
            this._cleanupStream(taskId);
            // Release the UI immediately, but retain recovery until the final state arrives.
            if (this._ownsProgress(taskId)) {
                const spinner = document.querySelector('#task-progress .spinner-border');
                spinner?.classList.add('d-none');
                this.setInputAreaDisabled(false);
                if (error) this.onTaskError(taskId, error);
            }
            this.fetchTaskState(taskId, { terminal: true });
        }

        startWebSocket(taskId) {
            taskId = String(taskId);
            const stream = this.activeStreams.get(taskId);
            if (!stream) return;
            this._clearTimer(stream, 'reconnectTimer');
            if (stream.socket && (stream.socket.readyState === WebSocket.OPEN || stream.socket.readyState === WebSocket.CONNECTING)) {
                return;
            }
            const protocol = window.location.protocol === "https:" ? "wss" : "ws";
            const wsUrl = `${protocol}://${window.location.host}/ws/task/${taskId}/`;
            const socket = new WebSocket(wsUrl);
            stream.socket = socket;

            const requestSidebarRefresh = (payload) => {
                if (window.FileManager?.requestSidebarRefresh) {
                    window.FileManager.requestSidebarRefresh(payload);
                    return;
                }
                document.dispatchEvent(new CustomEvent('nova:sidebar-refresh-request', { detail: payload }));
            };

            const messageHandlers = {
                'pong': (data) => {
                    this._clearTimer(stream, 'heartbeatTimeout');
                },
                'task_snapshot': (data) => {
                    const terminal = ['COMPLETED', 'FAILED', 'INTERRUPTED', 'DISPATCH_FAILED', 'CANCELED'].includes(
                        String(data.status || '').toUpperCase()
                    );
                    this.applyTaskSnapshot(taskId, data, { terminal, requestedEventCounter: stream.localEventCounter });
                },
                'progress_update': (data) => {
                    if (data.thread_id && !this._belongsToCurrentThread(stream, data.thread_id)) return;
                    const progressLogs = document.getElementById('progress-logs');
                    const log = data.progress_log || "undefined";
                    if (progressLogs) progressLogs.textContent = log;
                    this.messageManager?.scheduleExecutionTraceRefresh(taskId);
                },
                'response_chunk': (data) => {
                    if (!this._belongsToCurrentThread(stream, data.thread_id)) return;
                    stream.hasLiveContent = true;
                    this.onStreamChunk(taskId, data.chunk);
                },
                'context_consumption': (data) => {
                    if (!this._belongsToCurrentThread(stream, data.thread_id) || !stream.element) return;
                    const streamingFooter = stream.element.querySelector?.('.card-footer-consumption');
                    const streamingFooterWrapper = stream.element.querySelector?.('.agent-message-footer');
                    if (streamingFooter) {
                        const contextPayload = {
                            real_tokens: data.real_tokens,
                            approx_tokens: data.approx_tokens,
                            max_context: data.max_context,
                        };
                        streamingFooter.innerHTML = window.MessageRenderer.renderContextFooterChipContent(contextPayload);
                        streamingFooter.classList.toggle('d-none', !streamingFooter.textContent.trim());
                        if (stream.element?.dataset) {
                            stream.element.dataset.contextRealTokens =
                                data.real_tokens !== null && data.real_tokens !== undefined
                                    ? String(data.real_tokens)
                                    : '';
                            stream.element.dataset.contextApproxTokens =
                                data.approx_tokens !== null && data.approx_tokens !== undefined
                                    ? String(data.approx_tokens)
                                    : '';
                            stream.element.dataset.contextMaxContext =
                                data.max_context !== null && data.max_context !== undefined
                                    ? String(data.max_context)
                                    : '';
                            stream.element.dataset.contextLegacyTokens = '';
                        }
                        if (streamingFooter.textContent.trim() && streamingFooterWrapper) {
                            streamingFooterWrapper.classList.remove('d-none');
                        }
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
                    if (!this._belongsToCurrentThread(stream, data.thread_id)) return;
                    this.onNewMessage(data.message, data.thread_id, data.task_id || null);
                },
                'task_complete': (data) => {
                    if (data.thread_id && !this._belongsToCurrentThread(stream, data.thread_id)) return;
                    // Update thread title in sidebars if backend provided it
                    if (data.thread_id && data.thread_subject) {
                        this.updateThreadSubject(data.thread_id, data.thread_subject);
                    }
                    this.messageManager?.scheduleExecutionTraceRefresh(taskId);
                    this.reconcileTerminalEvent(taskId);
                    requestSidebarRefresh({
                        files: true,
                        webapps: true,
                        reason: 'task_end_fallback',
                        source: 'task-ws',
                    });
                },
                'thread_subject_updated': (data) => {
                    this.updateThreadSubject(data.thread_id, data.thread_subject);
                },
                'user_prompt': (data) => {
                    if (!this._belongsToCurrentThread(stream, data.thread_id)) return;
                    this.messageManager?.scheduleExecutionTraceRefresh(taskId);
                    this.onUserPrompt(taskId, data);
                },
                'interaction_update': (data) => {
                    if (!this._belongsToCurrentThread(stream, data.thread_id)) return;
                    this.messageManager?.scheduleExecutionTraceRefresh(taskId);
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
                    if (data.thread_id && !this._belongsToCurrentThread(stream, data.thread_id)) return;
                    this.messageManager?.scheduleExecutionTraceRefresh(taskId);
                    this.reconcileTerminalEvent(taskId, data);
                    requestSidebarRefresh({
                        files: true,
                        webapps: true,
                        reason: 'task_end_fallback',
                        source: 'task-ws',
                    });
                },
                'summarization_complete': (data) => {
                    this.onSummarizationComplete(data);
                }
            };

            socket.onmessage = (event) => {
                if (this.activeStreams.get(taskId) !== stream || stream.socket !== socket
                    || !this._belongsToCurrentThread(stream)) return;
                try {
                    const data = JSON.parse(event.data);
                    const handler = messageHandlers[data.type];
                    if (handler) handler(data);
                } catch (error) {
                    console.warn('Invalid task websocket message:', error);
                }
            };

            socket.onopen = () => {
                if (this.activeStreams.get(taskId) !== stream || stream.socket !== socket) return;
                stream.reconnectAttempts = 0;
                stream.heartbeatInterval = window.setInterval(() => {
                    if (socket.readyState !== WebSocket.OPEN) return;
                    socket.send(JSON.stringify({ type: 'ping' }));
                    this._clearTimer(stream, 'heartbeatTimeout');
                    stream.heartbeatTimeout = window.setTimeout(() => {
                        if (stream.socket === socket && socket.readyState === WebSocket.OPEN) socket.close();
                    }, 10000);
                }, 30000);
                this.fetchTaskState(taskId);
            };

            socket.onclose = (event) => {
                if (stream.socket !== socket) return;
                this._clearTimer(stream, 'heartbeatTimeout');
                if (stream.heartbeatInterval) window.clearInterval(stream.heartbeatInterval);
                stream.heartbeatInterval = null;
                stream.socket = null;
                if (this.activeStreams.get(taskId) !== stream || stream.status === 'completed' || stream.status === 'error') return;
                stream.status = 'reconnecting';
                this._scheduleReconnect(taskId, event.code === 4403);
                this.fetchTaskState(taskId);
            };

            socket.onerror = (err) => {
                console.warn('Task websocket unavailable:', err);
            };
        }

        _scheduleReconnect(taskId, accessDenied = false) {
            const stream = this.activeStreams.get(String(taskId));
            if (!stream || stream.terminalEvent) return;
            if (accessDenied || stream.reconnectAttempts >= this.maxReconnectAttempts) {
                this.fetchTaskState(taskId);
                return;
            }
            this._clearTimer(stream, 'reconnectTimer');
            const delay = accessDenied ? 15000 : this.reconnectDelays[stream.reconnectAttempts] || 15000;
            stream.reconnectAttempts += 1;
            stream.reconnectTimer = window.setTimeout(() => {
                stream.reconnectTimer = null;
                this.startWebSocket(taskId);
            }, delay);
        }

        async fetchTaskState(taskId, { terminal = false } = {}) {
            const normalizedTaskId = String(taskId);
            const stream = this.activeStreams.get(normalizedTaskId);
            if (!stream) return undefined;
            if (stream.stateRequest) {
                if (!terminal) return stream.stateRequest;
                return stream.stateRequest.then(() => this.fetchTaskState(normalizedTaskId, { terminal: true }));
            }
            const requestedEventCounter = stream.localEventCounter;
            const controller = new AbortController();
            const requestTimeout = window.setTimeout(() => controller.abort(), 10000);
            stream.stateRequest = fetch(this._stateUrl(normalizedTaskId), {
                headers: { 'X-AJAX': 'true' }, signal: controller.signal, cache: 'no-store',
            })
                .then(async (response) => {
                    if ([403, 404].includes(response.status)) {
                        if (this.activeStreams.get(normalizedTaskId) === stream) {
                            this.onStreamComplete(normalizedTaskId);
                        }
                        return null;
                    }
                    if (!response.ok) throw new Error(`Task state request failed: ${response.status}`);
                    return response.json();
                })
                .then((snapshot) => {
                    if (this.activeStreams.get(normalizedTaskId) === stream) {
                        this.applyTaskSnapshot(normalizedTaskId, snapshot, { requestedEventCounter });
                    }
                })
                .catch((error) => {
                    if (terminal) console.warn('Unable to reconcile task state:', error);
                })
                .finally(() => {
                    window.clearTimeout(requestTimeout);
                    const current = this.activeStreams.get(normalizedTaskId);
                    if (current !== stream) return;
                    current.stateRequest = null;
                    if (current.status === 'running' || current.status === 'streaming' || current.status === 'reconnecting') {
                        this._clearTimer(current, 'pollTimer');
                        current.pollTimer = window.setTimeout(() => this.fetchTaskState(normalizedTaskId), this.statePollMs);
                    }
                });
            return stream.stateRequest;
        }

        applyTaskSnapshot(taskId, snapshot, { terminal = false, requestedEventCounter = 0 } = {}) {
            const stream = this.activeStreams.get(String(taskId));
            if (!stream || !snapshot || (snapshot.thread_id && !this._belongsToCurrentThread(stream, snapshot.thread_id))) return;
            const revision = Number(snapshot.revision);
            const updatedAt = Date.parse(snapshot.updated_at || '');
            if (Number.isFinite(revision) && revision < stream.revision) return;
            if (!Number.isFinite(revision) && Number.isFinite(updatedAt) && updatedAt < stream.updatedAt) return;
            if (!terminal && !Number.isFinite(revision) && !Number.isFinite(updatedAt)
                && requestedEventCounter < stream.localEventCounter) return;
            if (Number.isFinite(revision)) stream.revision = revision;
            if (Number.isFinite(updatedAt)) stream.updatedAt = updatedAt;
            const shouldRestoreResponse = Boolean(snapshot.current_response)
                && !stream.hasLiveContent && !stream.finalMessageReceived;
            if (shouldRestoreResponse) this.onStreamChunk(taskId, snapshot.current_response);
            if (snapshot.last_progress && !stream.terminalEvent && this._ownsProgress(taskId)) {
                const progress = document.getElementById('progress-logs');
                if (progress) progress.textContent = snapshot.last_progress.step || snapshot.last_progress.message || snapshot.last_progress;
            }
            (Array.isArray(snapshot.messages) ? snapshot.messages : []).forEach((message) => this.onNewMessage(message, snapshot.thread_id, taskId));
            (Array.isArray(snapshot.interactions) ? snapshot.interactions : []).forEach((interaction) => {
                if (interaction.type === 'interaction_update' || interaction.interaction_status) this.onInteractionUpdate(taskId, interaction);
                else this.onUserPrompt(taskId, interaction);
            });
            const status = String(snapshot.status || '').toLowerCase();
            if (['completed', 'complete', 'failed', 'error', 'interrupted', 'dispatch_failed', 'canceled', 'cancelled'].includes(status)) {
                if (status !== 'completed' && status !== 'complete') this.onTaskError(taskId, { message: snapshot.result });
                this.onStreamComplete(taskId);
            }
        }

        // Register background task (non-streaming operations like compact, delete)
        registerBackgroundTask(taskId) {
            const normalizedTaskId = String(taskId);
            const threadId = this._currentThreadId();
            this.activeStreams.set(normalizedTaskId, {
                taskId: normalizedTaskId,
                messageId: normalizedTaskId,
                element: null,
                placeholderActive: false,
                status: 'running',
                threadId,
                revision: 0,
                updatedAt: 0,
                localEventCounter: 0,
                reconnectAttempts: 0,
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
            this.setExecutionTraceButton(normalizedTaskId, true);

            // Start WebSocket connection for progress updates
            this.startWebSocket(normalizedTaskId);
            this.fetchTaskState(normalizedTaskId);
        }

        // Reconnect to an existing task (when user returns to page)
        reconnectToTask(taskId, currentResponse, lastProgress) {
            // Check if already connected
            if (this.activeStreams.has(String(taskId))) {
                return;
            }

            // Register the stream with reconnect flag
            const normalizedTaskId = String(taskId);
            this.activeStreams.set(normalizedTaskId, {
                taskId: normalizedTaskId,
                messageId: normalizedTaskId,
                element: null,
                status: 'reconnecting',
                isReconnect: true,
                lastChunk: currentResponse || '',
                placeholderActive: Boolean(currentResponse),
                threadId: this._currentThreadId(),
                revision: 0,
                updatedAt: 0,
                localEventCounter: 0,
                reconnectAttempts: 0,
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
            this.setExecutionTraceButton(normalizedTaskId, true);

            // Set last known progress message
            const progressLogs = document.getElementById('progress-logs');
            if (progressLogs && lastProgress) {
                progressLogs.textContent = lastProgress.step || 'Reconnecting...';
            }

            // Disable input area while task is running
            this.setInputAreaDisabled(true);

            // If we have current response, show it immediately
            if (currentResponse) {
                const stream = this.activeStreams.get(normalizedTaskId);
                stream.element = this.createMessageElement(normalizedTaskId);
                const contentEl = stream.element.querySelector('.streaming-content');
                contentEl.innerHTML = currentResponse;
            }

            // Start WebSocket connection for live updates
            this.startWebSocket(normalizedTaskId);
            this.fetchTaskState(normalizedTaskId);
        }

        // Handle real-time message updates like system messages
        onNewMessage(messageData, thread_id, taskId = null) {
            if (!messageData || messageData.id === undefined || messageData.id === null) return;
            const normalizedTaskId = taskId === null ? null : String(taskId);
            const stream = normalizedTaskId ? this.activeStreams.get(normalizedTaskId) : null;
            if (stream && !this._belongsToCurrentThread(stream, thread_id)) return;
            if (String(thread_id) !== this._currentThreadId()) return;
            const existing = document.getElementById(`message-${messageData.id}`);
            if (existing) {
                if (stream?.placeholderActive && stream.element !== existing) stream.element?.remove();
                if (stream) {
                    stream.element = existing;
                    stream.placeholderActive = false;
                    stream.finalMessageReceived = true;
                }
                return;
            }
            const messageElement = window.MessageRenderer.createMessageElement(messageData, thread_id);
            if (stream?.placeholderActive && stream.element?.isConnected) {
                stream.element.replaceWith(messageElement);
            } else {
                this.messageManager?.appendMessage(messageElement);
            }
            if (stream) {
                stream.element = messageElement;
                stream.placeholderActive = false;
                stream.finalMessageReceived = true;
            }
            this.messageManager?.updateCompactLinkVisibility();
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

        _getInteractionChoiceOptions(schema) {
            if (!schema || typeof schema !== 'object') {
                return [];
            }
            if (schema.type === 'boolean') {
                return [
                    { label: gettext('Yes'), value: true },
                    { label: gettext('No'), value: false },
                ];
            }
            const enumValues = Array.isArray(schema.enum) ? schema.enum : [];
            if (!enumValues.length || enumValues.length > 5) {
                return [];
            }
            const supportedValues = enumValues.filter((value) => (
                value === null
                || ['string', 'number', 'boolean'].includes(typeof value)
            ));
            if (!supportedValues.length || supportedValues.length !== enumValues.length) {
                return [];
            }
            return supportedValues.map((value) => {
                if (value === true) {
                    return { label: gettext('Yes'), value };
                }
                if (value === false) {
                    return { label: gettext('No'), value };
                }
                return { label: String(value), value };
            });
        }

        _renderInteractionChoiceControls(interactionId, schema) {
            const choices = this._getInteractionChoiceOptions(schema);
            if (!choices.length) {
                return '';
            }
            const buttons = choices.map((choice) => `
              <button
                type="button"
                class="btn btn-sm btn-outline-primary interaction-answer-btn"
                data-interaction-id="${interactionId}"
                data-answer-json="${window.DOMUtils.escapeHTML(JSON.stringify(choice.value))}"
              >
                ${window.DOMUtils.escapeHTML(choice.label)}
              </button>
            `).join('');
            return `
              <div class="small text-muted mb-2">${gettext('Quick choices')}</div>
              <div class="d-flex flex-wrap gap-2">${buttons}</div>
            `;
        }

        _buildInteractionSchemaHint(schema) {
            if (!schema || Object.keys(schema).length === 0) {
                return '';
            }
            const choices = this._getInteractionChoiceOptions(schema);
            if (choices.length) {
                return gettext('Quick choices are available; plain text is also accepted.');
            }
            return gettext('Answer format may be structured; plain text is also accepted.');
        }

        hydratePendingInteractionCards() {
            const cards = document.querySelectorAll('[data-interaction-id][data-interaction-schema]');
            cards.forEach((card) => {
                let schema = {};
                const rawSchema = card.dataset.interactionSchema || '';
                if (rawSchema) {
                    try {
                        schema = JSON.parse(rawSchema);
                    } catch (_error) {
                        schema = {};
                    }
                }
                const choiceContainer = card.querySelector('.interaction-choice-controls');
                if (choiceContainer) {
                    choiceContainer.innerHTML = this._renderInteractionChoiceControls(
                        card.dataset.interactionId || '',
                        schema,
                    );
                }
                const schemaHint = card.querySelector('.interaction-schema-hint');
                if (schemaHint) {
                    const hint = this._buildInteractionSchemaHint(schema);
                    schemaHint.textContent = hint;
                    schemaHint.classList.toggle('d-none', !hint);
                }
            });
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
            if (!interaction_id || document.getElementById(`interaction-card-${interaction_id}`)) return;

            // Build card element from template
            const wrapper = document.createElement('div');
            wrapper.className = 'message mb-3';
            wrapper.id = `interaction-card-${interaction_id}`;
            wrapper.dataset.interactionId = String(interaction_id);
            wrapper.dataset.interactionSchema = JSON.stringify(schema || {});

            const origin = origin_name ? `${window.DOMUtils.escapeHTML(origin_name)} ${gettext('asks')}:` : gettext('Question');
            const choiceControls = this._renderInteractionChoiceControls(interaction_id, schema);
            const schemaHint = this._buildInteractionSchemaHint(schema);

            wrapper.innerHTML = `
        <div class="card border-warning">
          <div class="card-body">
            <div class="d-flex align-items-center mb-2">
              <i class="bi bi-question-circle text-warning me-2"></i>
              <strong>${origin}</strong>
            </div>
            <div class="mb-2">${window.DOMUtils.escapeHTML(question)}</div>
            <div class="interaction-choice-controls mb-2">${choiceControls}</div>
            <div class="mb-2">
              <textarea class="form-control" id="interaction-answer-input-${interaction_id}" rows="2" placeholder="${gettext('Type your answer...')}"></textarea>
              <div class="form-text text-muted mt-1 interaction-schema-hint ${schemaHint ? '' : 'd-none'}">${window.DOMUtils.escapeHTML(schemaHint)}</div>
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
            const hasPendingInteraction = document.querySelector('[data-interaction-id]:not(.d-none)');
            this.setInputAreaDisabled(Boolean(hasPendingInteraction));

            // Hide card after 2 seconds
            setTimeout(() => {
                card.classList.add('d-none');
            }, 2000);
        }

        onTaskError(taskId, error) {
            if (!this._ownsProgress(taskId)) return;
            // Stop the spinner
            const spinner = document.querySelector('#task-progress .spinner-border');
            if (spinner) {
                spinner.classList.add('d-none');
            }
            // Show error message
            const progressLogs = document.getElementById('progress-logs');
            if (progressLogs) {
                progressLogs.textContent = error.message || gettext('An error occurred.');
            }
            this.setExecutionTraceButton(taskId, true);
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
