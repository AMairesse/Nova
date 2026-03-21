(function () {
    'use strict';

    window.NovaApp = window.NovaApp || {};
    window.NovaApp.Modules = window.NovaApp.Modules || {};

    window.NovaApp.Modules.MessageComposerMethods = {
        async handleFormSubmit(form) {
            const textarea = form.querySelector('textarea[name="new_message"]');
            const originalMessage = textarea ? textarea.value : '';
            const msg = originalMessage.trim();
            const hasAttachments = this.composerAttachments.length > 0;
            const hasThreadFiles = this.composerThreadFiles.length > 0;
            if (!msg && !hasAttachments && !hasThreadFiles) return;
            if (this.isComposerSubmitting) return;

            const blockingCapabilityError = this.getComposerBlockingCapabilityError();
            if (blockingCapabilityError) {
                this.showToast(blockingCapabilityError, 'danger');
                return;
            }

            this.isComposerSubmitting = true;

            const sendBtn = document.getElementById('send-btn');
            if (sendBtn) {
                sendBtn.disabled = true;
                sendBtn.innerHTML = '<i class="bi bi-hourglass-split"></i>';
            }

            if (textarea) {
                textarea.value = '';
                this.resizeComposerTextarea(textarea);
                this.syncComposerTextStatus(textarea);
            }

            try {
                const formData = new FormData(form);
                formData.set(
                    'new_message',
                    this.buildComposerSubmissionMessage(originalMessage)
                );
                for (const attachment of this.composerAttachments) {
                    formData.append('message_attachments', attachment.file, attachment.file.name);
                }
                for (const threadFile of this.composerThreadFiles) {
                    formData.append('files', threadFile.file, threadFile.file.name);
                }

                const response = await window.DOMUtils.csrfFetch(
                    window.NovaApp.urls.addMessage,
                    {
                        method: 'POST',
                        body: formData
                    }
                );

                const isJsonResponse = (response.headers.get('content-type') || '').includes(
                    'application/json'
                );
                const data = isJsonResponse ? await response.json() : null;
                const errorMessage =
                    data?.message || data?.error || `Request failed (${response.status})`;
                if (!response.ok || !data || data.status !== 'OK') {
                    throw new Error(errorMessage);
                }

                const threadIdInput = document.querySelector('input[name="thread_id"]');
                if (threadIdInput) threadIdInput.value = data.thread_id;
                this.currentThreadId = data.thread_id;
                this.resetComposerAttachments();
                this.resetComposerThreadFiles();

                const userMessageEl = window.MessageRenderer.createMessageElement(
                    data.message,
                    ''
                );
                this.appendMessage(userMessageEl);
                this.scrollToMessage(data.message.id);

                this.streamingManager.registerStream(data.task_id, {
                    id: data.task_id,
                    actor: 'agent',
                    text: ''
                });

                document.dispatchEvent(
                    new CustomEvent('nova:message-posted', { detail: data })
                );
            } catch (error) {
                console.error('Error sending message:', error);
                if (textarea) {
                    textarea.value = originalMessage;
                    this.resizeComposerTextarea(textarea);
                    this.syncComposerTextStatus(textarea);
                    textarea.focus();
                }
                if (sendBtn) {
                    sendBtn.disabled = false;
                    sendBtn.innerHTML = '<i class="bi bi-send-fill"></i>';
                }
                this.showToast(
                    error?.message || gettext('Failed to send message.'),
                    'danger'
                );
            } finally {
                this.isComposerSubmitting = false;
            }
        },

        triggerComposerSubmit(form) {
            if (!form || this.isComposerSubmitting) return;
            return this.handleFormSubmit(form);
        },

        resizeComposerTextarea(textarea) {
            if (!textarea) return;
            const viewportHeight = Number.isFinite(window.innerHeight)
                ? window.innerHeight
                : 960;
            const maxHeight = Math.min(Math.round(viewportHeight * 0.4), 384);
            textarea.style.height = 'auto';
            textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
        },

        syncComposerAttachmentConfig() {
            const form = document.getElementById('message-form');
            const dataset = form?.dataset || {};
            const maxFiles = Number.parseInt(
                dataset.messageAttachmentMaxFiles || '',
                10
            );
            const maxImageBytes = Number.parseInt(
                dataset.messageAttachmentMaxImageBytes || '',
                10
            );
            const maxDocumentBytes = Number.parseInt(
                dataset.messageAttachmentMaxDocumentBytes || '',
                10
            );
            const maxAudioBytes = Number.parseInt(
                dataset.messageAttachmentMaxAudioBytes || '',
                10
            );
            const softTextLimit = Number.parseInt(
                dataset.composerSoftTextLimit || '',
                10
            );
            const hardTextLimit = Number.parseInt(
                dataset.composerHardTextLimit || '',
                10
            );
            const sizeLabel = `${dataset.messageAttachmentMaxSizeLabel || ''}`.trim();

            if (Number.isFinite(maxFiles) && maxFiles > 0) {
                this.maxComposerAttachments = maxFiles;
            }
            if (Number.isFinite(maxImageBytes) && maxImageBytes > 0) {
                this.maxComposerImageBytes = maxImageBytes;
            }
            if (Number.isFinite(maxDocumentBytes) && maxDocumentBytes > 0) {
                this.maxComposerDocumentBytes = maxDocumentBytes;
            }
            if (Number.isFinite(maxAudioBytes) && maxAudioBytes > 0) {
                this.maxComposerAudioBytes = maxAudioBytes;
            }
            if (Number.isFinite(softTextLimit) && softTextLimit > 0) {
                this.maxComposerSoftTextLimit = softTextLimit;
            }
            if (Number.isFinite(hardTextLimit) && hardTextLimit > this.maxComposerSoftTextLimit) {
                this.maxComposerHardTextLimit = hardTextLimit;
            }
            this.composerAttachmentSizeLabel =
                sizeLabel || this.formatAttachmentSizeLabel(this.maxComposerImageBytes);

            const textarea = form?.querySelector('textarea[name="new_message"]');
            if (textarea) {
                textarea.maxLength = this.maxComposerHardTextLimit;
                this.resizeComposerTextarea(textarea);
            }
            this.syncComposerTextStatus(textarea);
        },

        syncComposerTextStatus(textarea = null) {
            const targetTextarea =
                textarea ||
                document.querySelector('#message-container textarea[name="new_message"]');
            const status = document.getElementById('composer-status-line');
            if (!status) return;

            const currentLength = `${targetTextarea?.value || ''}`.length;
            const pendingFilesCount = this.composerThreadFiles.length;

            if (
                currentLength < this.maxComposerSoftTextLimit &&
                pendingFilesCount === 0
            ) {
                status.textContent = '';
                status.className = 'composer-status-line small text-muted mt-2 d-none';
                return;
            }

            const parts = [
                this.interpolateMessage(
                    gettext('%(count)s / %(limit)s characters'),
                    {
                        count: currentLength,
                        limit: this.maxComposerHardTextLimit,
                    }
                ),
            ];
            let tone = 'muted';

            if (currentLength >= this.maxComposerHardTextLimit) {
                tone = 'danger';
                parts.push(
                    gettext(
                        'Large pasted text must be moved to Files once you reach this limit.'
                    )
                );
            } else if (currentLength >= this.maxComposerSoftTextLimit) {
                tone = 'warning';
                parts.push(
                    gettext(
                        'If you paste a large log or document, Nova can move it to Files.'
                    )
                );
            }

            if (pendingFilesCount > 0) {
                parts.push(
                    this.interpolateMessage(
                        gettext('%(count)s pending file(s) will be added to Files.'),
                        { count: pendingFilesCount }
                    )
                );
            }

            status.textContent = parts.join(' ');
            status.className = `composer-status-line small mt-2 text-${tone}`;
        },

        formatAttachmentSizeLabel(sizeBytes) {
            const mib = 1024 * 1024;
            const kib = 1024;

            if (sizeBytes >= mib) {
                const sizeMb = sizeBytes / mib;
                return Number.isInteger(sizeMb)
                    ? `${sizeMb} MB`
                    : `${sizeMb.toFixed(1)} MB`;
            }
            if (sizeBytes >= kib) {
                const sizeKb = sizeBytes / kib;
                return Number.isInteger(sizeKb)
                    ? `${sizeKb} KB`
                    : `${sizeKb.toFixed(1)} KB`;
            }
            return `${sizeBytes} bytes`;
        },

        interpolateMessage(template, params) {
            return Object.entries(params || {}).reduce((result, [key, value]) => {
                return result.replace(`%(${key})s`, String(value));
            }, template);
        },

        buildAttachmentCountLimitMessage() {
            return this.interpolateMessage(
                gettext('You can attach up to %(count)s images per message.'),
                { count: this.maxComposerAttachments }
            );
        },

        buildAttachmentSizeLimitMessage(sizeLabel, typeLabel) {
            return this.interpolateMessage(
                gettext('Each %(type)s attachment must be %(size)s or less.'),
                {
                    type: typeLabel,
                    size: sizeLabel,
                }
            );
        },

        openComposerAttachmentPicker(inputId) {
            const input = document.getElementById(inputId);
            if (input) input.click();
        },

        async handleComposerAttachmentInputChange(input) {
            const files = Array.from(input?.files || []);
            if (!files.length) return;
            try {
                await this.addComposerAttachments(files);
            } catch (error) {
                console.error('Error preparing attachments:', error);
                this.showToast(
                    gettext('Failed to prepare the selected image.'),
                    'danger'
                );
            } finally {
                input.value = '';
            }
        },

        async handleComposerPaste(event) {
            const textarea = event?.target;
            const clipboardData = event?.clipboardData;
            if (!textarea || !clipboardData) return;

            const items = Array.from(clipboardData.items || []);
            const acceptedAttachments = [];
            const rejectedBinaryItems = [];

            for (const item of items) {
                if (item?.kind !== 'file') continue;
                const file = item.getAsFile();
                if (!file) continue;

                const normalizedType = `${file.type || ''}`.toLowerCase();
                const normalizedName = `${file.name || ''}`.toLowerCase();
                if (
                    normalizedType.startsWith('image/') ||
                    normalizedType === 'application/pdf' ||
                    normalizedName.endsWith('.pdf')
                ) {
                    acceptedAttachments.push(file);
                } else {
                    rejectedBinaryItems.push(file);
                }
            }

            const textPayload = `${clipboardData.getData('text/plain') || ''}`;
            const selectionStart =
                typeof textarea.selectionStart === 'number'
                    ? textarea.selectionStart
                    : textarea.value.length;
            const selectionEnd =
                typeof textarea.selectionEnd === 'number'
                    ? textarea.selectionEnd
                    : selectionStart;
            const projectedLength =
                textarea.value.length -
                Math.max(0, selectionEnd - selectionStart) +
                textPayload.length;
            const hasBinaryItems =
                acceptedAttachments.length > 0 || rejectedBinaryItems.length > 0;
            const shouldInterceptText =
                Boolean(textPayload) &&
                projectedLength >= this.maxComposerSoftTextLimit;

            if (!hasBinaryItems && !shouldInterceptText) {
                return;
            }

            event.preventDefault();

            if (acceptedAttachments.length) {
                try {
                    await this.addComposerAttachments(acceptedAttachments);
                } catch (error) {
                    console.error('Error preparing pasted attachment:', error);
                    this.showToast(
                        gettext('Failed to prepare one of the pasted files.'),
                        'danger'
                    );
                }
            }

            if (rejectedBinaryItems.length) {
                this.showToast(
                    gettext(
                        'This pasted file type is not supported here. Drop it into Files instead.'
                    ),
                    'warning'
                );
            }

            if (!textPayload) {
                textarea.focus();
                return;
            }

            let decision = 'keep';
            if (projectedLength > this.maxComposerHardTextLimit) {
                decision = await this.openComposerPasteDecisionModal({
                    projectedLength,
                    pastedLength: textPayload.length,
                    forceFile: true,
                });
            } else if (projectedLength >= this.maxComposerSoftTextLimit) {
                decision = await this.openComposerPasteDecisionModal({
                    projectedLength,
                    pastedLength: textPayload.length,
                    forceFile: false,
                });
            }

            if (decision === 'cancel') {
                textarea.focus();
                this.syncComposerTextStatus(textarea);
                return;
            }
            if (decision === 'file') {
                await this.queueComposerThreadFileFromText(textPayload);
                textarea.focus();
                this.syncComposerTextStatus(textarea);
                return;
            }

            this.insertComposerText(textarea, textPayload);
        },

        async addComposerAttachments(files) {
            const accepted = [];
            const availableSlots =
                this.maxComposerAttachments - this.composerAttachments.length;
            if (availableSlots <= 0) {
                this.showToast(this.buildAttachmentCountLimitMessage(), 'warning');
                return;
            }

            for (const file of files) {
                if (accepted.length >= availableSlots) {
                    this.showToast(this.buildAttachmentCountLimitMessage(), 'warning');
                    break;
                }
                const kind = this.getComposerAttachmentKind(file);
                if (!kind) {
                    this.showToast(
                        gettext(
                            'Only image, PDF, and audio attachments are supported here.'
                        ),
                        'warning'
                    );
                    continue;
                }
                const maxSize = this.getComposerAttachmentMaxBytes(kind);
                const typeLabel = this.getComposerAttachmentTypeLabel(kind);
                if (file.size > maxSize) {
                    this.showToast(
                        this.buildAttachmentSizeLimitMessage(
                            this.formatAttachmentSizeLabel(maxSize),
                            typeLabel
                        ),
                        'warning'
                    );
                    continue;
                }
                const stableFile = await this.cloneComposerFile(file);
                accepted.push({
                    id:
                        window.crypto &&
                        typeof window.crypto.randomUUID === 'function'
                            ? window.crypto.randomUUID()
                            : `${Date.now()}-${Math.random()}`,
                    file: stableFile,
                    kind,
                    previewUrl:
                        kind === 'image'
                            ? URL.createObjectURL(stableFile)
                            : '',
                });
            }

            if (!accepted.length) return;
            this.composerAttachments.push(...accepted);
            this.renderComposerAttachments();
        },

        insertComposerText(textarea, text) {
            if (!textarea) return;
            const insertText = `${text || ''}`;
            const start =
                typeof textarea.selectionStart === 'number'
                    ? textarea.selectionStart
                    : textarea.value.length;
            const end =
                typeof textarea.selectionEnd === 'number'
                    ? textarea.selectionEnd
                    : start;

            if (typeof textarea.setRangeText === 'function') {
                textarea.setRangeText(insertText, start, end, 'end');
            } else {
                textarea.value =
                    `${textarea.value.slice(0, start)}${insertText}${textarea.value.slice(end)}`;
                const nextPos = start + insertText.length;
                textarea.selectionStart = nextPos;
                textarea.selectionEnd = nextPos;
            }

            this.resizeComposerTextarea(textarea);
            this.syncComposerTextStatus(textarea);
            textarea.focus();
        },

        openComposerPasteDecisionModal({
            projectedLength = 0,
            pastedLength = 0,
            forceFile = false,
        } = {}) {
            const modalEl = document.getElementById('composerPasteDecisionModal');
            const messageEl = document.getElementById(
                'composer-paste-decision-message'
            );
            const detailEl = document.getElementById(
                'composer-paste-decision-detail'
            );
            const keepBtn = document.getElementById('composer-paste-decision-keep');
            if (
                !modalEl ||
                !messageEl ||
                !detailEl ||
                !keepBtn ||
                !window.bootstrap?.Modal
            ) {
                return Promise.resolve(forceFile ? 'file' : 'keep');
            }

            if (this.pendingComposerPasteDecision?.resolve) {
                this.pendingComposerPasteDecision.resolve('cancel');
                if (this.pendingComposerPasteDecision.modal) {
                    this.pendingComposerPasteDecision.modal.hide();
                }
                this.pendingComposerPasteDecision = null;
            }

            messageEl.textContent = forceFile
                ? gettext(
                      'This pasted text is too large for the message box. Create a file in Files instead.'
                  )
                : gettext(
                      'This pasted text will make the message quite large. Do you want to keep it in the message or create a file in Files instead?'
                  );
            detailEl.textContent = this.interpolateMessage(
                gettext(
                    'Pasted block: %(pasted)s characters. Message after paste: %(total)s / %(limit)s.'
                ),
                {
                    pasted: pastedLength,
                    total: projectedLength,
                    limit: this.maxComposerHardTextLimit,
                }
            );
            keepBtn.classList.toggle('d-none', forceFile);

            const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
            return new Promise((resolve) => {
                this.pendingComposerPasteDecision = {
                    resolve,
                    modal,
                };
                modal.show();
            });
        },

        resolveComposerPasteDecision(decision) {
            const pending = this.pendingComposerPasteDecision;
            if (!pending) return;

            this.pendingComposerPasteDecision = null;
            if (pending.modal) {
                pending.modal.hide();
            }
            pending.resolve(decision || 'cancel');
        },

        buildComposerThreadFileName() {
            const now = new Date();
            const pad = (value) => String(value).padStart(2, '0');
            const stamp =
                `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}` +
                `-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;

            let candidate = `pasted-context-${stamp}.txt`;
            const existingNames = new Set(
                this.composerThreadFiles.map((item) => item.file?.name || '')
            );
            let suffix = 2;
            while (existingNames.has(candidate)) {
                candidate = `pasted-context-${stamp}-${suffix}.txt`;
                suffix += 1;
            }
            return candidate;
        },

        async queueComposerThreadFileFromText(text) {
            if (typeof File === 'undefined') {
                this.showToast(
                    gettext('Your browser cannot create a file from pasted text here.'),
                    'warning'
                );
                return;
            }

            const sourceText = `${text || ''}`;
            if (!sourceText) return;

            const rawFile = new File(
                [sourceText],
                this.buildComposerThreadFileName(),
                {
                    type: 'text/plain',
                    lastModified: Date.now(),
                }
            );
            const stableFile = await this.cloneComposerFile(rawFile);
            this.composerThreadFiles.push({
                id:
                    window.crypto &&
                    typeof window.crypto.randomUUID === 'function'
                        ? window.crypto.randomUUID()
                        : `${Date.now()}-${Math.random()}`,
                file: stableFile,
            });
            this.renderComposerThreadFiles();
        },

        async cloneComposerFile(file) {
            if (
                !file ||
                typeof file.arrayBuffer !== 'function' ||
                typeof File === 'undefined'
            ) {
                return file;
            }

            try {
                const buffer = await file.arrayBuffer();
                return new File([buffer], file.name || 'image', {
                    type: file.type || 'application/octet-stream',
                    lastModified: file.lastModified || Date.now(),
                });
            } catch (error) {
                console.warn(
                    'Falling back to original File object for attachment:',
                    error
                );
                return file;
            }
        },

        removeComposerAttachment(attachmentId) {
            const nextAttachments = [];
            for (const attachment of this.composerAttachments) {
                if (attachment.id === attachmentId) {
                    if (attachment.previewUrl) {
                        URL.revokeObjectURL(attachment.previewUrl);
                    }
                    continue;
                }
                nextAttachments.push(attachment);
            }
            this.composerAttachments = nextAttachments;
            this.renderComposerAttachments();
        },

        resetComposerAttachments() {
            for (const attachment of this.composerAttachments) {
                if (attachment.previewUrl) {
                    URL.revokeObjectURL(attachment.previewUrl);
                }
            }
            this.composerAttachments = [];

            const galleryInput = document.getElementById('message-attachment-input');
            const cameraInput = document.getElementById('message-camera-input');
            if (galleryInput) galleryInput.value = '';
            if (cameraInput) cameraInput.value = '';

            this.renderComposerAttachments();
            this.syncComposerCapabilityNotice();
        },

        renderComposerAttachments() {
            const container = document.getElementById('composer-attachments');
            if (!container) return;
            if (!this.composerAttachments.length) {
                container.innerHTML = '';
                container.classList.add('d-none');
                this.syncComposerCapabilityNotice();
                this.syncComposerTextStatus();
                return;
            }

            container.classList.remove('d-none');
            container.innerHTML = this.composerAttachments
                .map((attachment) => `
                <div class="composer-attachment-chip">
                    ${attachment.kind === 'image'
                        ? `<img src="${attachment.previewUrl}" alt="${window.DOMUtils.escapeHTML(attachment.file.name)}" class="composer-attachment-thumb">`
                        : `<div class="composer-attachment-thumb composer-attachment-thumb-placeholder"><i class="bi ${attachment.kind === 'pdf' ? 'bi-filetype-pdf' : 'bi-file-earmark-music'}"></i></div>`
                    }
                    <div class="composer-attachment-meta">
                        <div class="composer-attachment-name">${window.DOMUtils.escapeHTML(attachment.file.name)}</div>
                        <div class="composer-attachment-size">${attachment.kind.toUpperCase()} · ${Math.max(1, Math.round(attachment.file.size / 1024))} KB</div>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-secondary composer-attachment-remove" data-attachment-id="${attachment.id}" aria-label="${gettext('Remove attachment')}">
                        <i class="bi bi-x-lg"></i>
                    </button>
                </div>
            `)
                .join('');
            this.syncComposerCapabilityNotice();
            this.syncComposerTextStatus();
        },

        removeComposerThreadFile(threadFileId) {
            this.composerThreadFiles = this.composerThreadFiles.filter(
                (item) => item.id !== threadFileId
            );
            this.renderComposerThreadFiles();
        },

        resetComposerThreadFiles() {
            if (this.pendingComposerPasteDecision?.modal) {
                this.pendingComposerPasteDecision.modal.hide();
            }
            if (this.pendingComposerPasteDecision?.resolve) {
                this.pendingComposerPasteDecision.resolve('cancel');
            }
            this.pendingComposerPasteDecision = null;
            this.composerThreadFiles = [];
            this.renderComposerThreadFiles();
        },

        renderComposerThreadFiles() {
            const container = document.getElementById('composer-thread-files');
            if (!container) return;

            if (!this.composerThreadFiles.length) {
                container.innerHTML = '';
                container.classList.add('d-none');
                this.syncComposerTextStatus();
                return;
            }

            container.classList.remove('d-none');
            container.innerHTML = this.composerThreadFiles
                .map((threadFile) => `
                <div class="composer-thread-file-chip">
                    <div class="composer-thread-file-icon">
                        <i class="bi bi-file-earmark-text"></i>
                    </div>
                    <div class="composer-thread-file-meta">
                        <div class="composer-thread-file-name">${window.DOMUtils.escapeHTML(threadFile.file.name)}</div>
                        <div class="composer-thread-file-size">FILES · ${Math.max(1, Math.round(threadFile.file.size / 1024))} KB</div>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-secondary composer-thread-file-remove" data-thread-file-id="${threadFile.id}" aria-label="${gettext('Remove file')}">
                        <i class="bi bi-x-lg"></i>
                    </button>
                </div>
            `)
                .join('');
            this.syncComposerTextStatus();
        },

        buildComposerSubmissionMessage(originalMessage) {
            if (!this.composerThreadFiles.length) {
                return originalMessage;
            }

            const introLines = [
                gettext(
                    'I added the following file(s) to Files because their pasted content was too large for the message box. Please review them first if relevant before answering:'
                ),
                ...this.composerThreadFiles.map(
                    (threadFile) => `- ${threadFile.file.name}`
                ),
            ];
            const prefix = introLines.join('\n');
            return `${prefix}${originalMessage.trim() ? `\n\n${originalMessage}` : ''}`;
        },

        getSelectedAgentCapabilityState() {
            const selectedAgentInput = document.getElementById('selectedAgentInput');
            const selectedAgentId = selectedAgentInput?.value;
            if (!selectedAgentId) return null;

            const selectedItem = document.querySelector(
                `.agent-dropdown-item[data-value="${selectedAgentId}"]`
            );
            if (!selectedItem) return null;

            return {
                verificationStatus: `${selectedItem.dataset.providerVerificationStatus || ''}`.trim(),
                providerType: `${selectedItem.dataset.providerType || ''}`.trim(),
                toolsStatus: `${selectedItem.dataset.providerToolsStatus || ''}`.trim(),
                visionStatus: `${selectedItem.dataset.providerVisionStatus || ''}`.trim(),
                imageStatus: `${selectedItem.dataset.providerImageStatus || ''}`.trim(),
                pdfStatus: `${selectedItem.dataset.providerPdfStatus || ''}`.trim(),
                audioStatus: `${selectedItem.dataset.providerAudioStatus || ''}`.trim(),
                imageOutputStatus: `${selectedItem.dataset.providerImageOutputStatus || ''}`.trim(),
                audioOutputStatus: `${selectedItem.dataset.providerAudioOutputStatus || ''}`.trim(),
                agentRequiresTools:
                    `${selectedItem.dataset.agentRequiresTools || ''}`.trim() ===
                    'true',
            };
        },

        getSelectedResponseMode() {
            const input = document.getElementById('responseModeInput');
            return `${input?.value || 'auto'}`.trim() || 'auto';
        },

        updateResponseModeButton(button, value) {
            if (!button) return;
            const normalizedValue = `${value || 'auto'}`.trim() || 'auto';
            const labels = {
                auto: gettext('Automatic'),
                text: gettext('Text only'),
                image: gettext('Image'),
                audio: gettext('Audio'),
            };
            button.textContent = labels[normalizedValue] || labels.auto;
        },

        shouldShowResponseModeControl(state) {
            if (!state) return false;
            if (this.getSelectedResponseMode() !== 'auto') return true;

            const providerType = `${state.providerType || ''}`.trim();
            if (providerType === 'openrouter') return true;
            return (
                state.imageOutputStatus === 'pass' ||
                state.audioOutputStatus === 'pass'
            );
        },

        syncResponseModeControl() {
            const row = document.getElementById('composer-output-mode-row');
            const button = document.getElementById('response-mode-chip');
            const state = this.getSelectedAgentCapabilityState();
            if (!row || !button) return;

            this.updateResponseModeButton(button, this.getSelectedResponseMode());
            row.classList.toggle(
                'd-none',
                !this.shouldShowResponseModeControl(state)
            );
        },

        getComposerBlockingCapabilityError() {
            const state = this.getSelectedAgentCapabilityState();
            if (!state) return '';

            const responseMode = this.getSelectedResponseMode();
            const {
                toolsStatus,
                visionStatus,
                imageStatus,
                pdfStatus,
                audioStatus,
                imageOutputStatus,
                audioOutputStatus,
                agentRequiresTools,
            } = state;
            const kinds = Array.from(
                new Set(this.composerAttachments.map((attachment) => attachment.kind))
            );

            if (
                (toolsStatus === 'fail' || toolsStatus === 'unsupported') &&
                agentRequiresTools
            ) {
                return gettext(
                    'The selected agent depends on tools or sub-agents, but this provider/model was explicitly verified without tool support.'
                );
            }

            if (
                responseMode === 'image' &&
                imageOutputStatus === 'unsupported'
            ) {
                return gettext(
                    'The selected agent provider was explicitly verified without image output support.'
                );
            }
            if (
                responseMode === 'audio' &&
                audioOutputStatus === 'unsupported'
            ) {
                return gettext(
                    'The selected agent provider was explicitly verified without audio output support.'
                );
            }

            const imageUnsupported =
                kinds.includes('image') &&
                (
                    imageStatus === 'unsupported' ||
                    visionStatus === 'fail' ||
                    visionStatus === 'unsupported'
                );
            const pdfUnsupported =
                kinds.includes('pdf') && pdfStatus === 'unsupported';
            const audioUnsupported =
                kinds.includes('audio') && audioStatus === 'unsupported';
            if (imageUnsupported || pdfUnsupported || audioUnsupported) {
                return gettext(
                    'The selected agent provider was explicitly verified without support for one of the attached input types.'
                );
            }
            return '';
        },

        syncComposerCapabilityNotice() {
            const note = document.getElementById(
                'composer-provider-capability-note'
            );
            if (!note) return;
            this.syncResponseModeControl();

            const hasAttachments = this.composerAttachments.length > 0;
            const state = this.getSelectedAgentCapabilityState();
            if (!state) {
                note.className = 'alert py-2 px-3 small mt-2 d-none';
                note.textContent = '';
                return;
            }

            const responseMode = this.getSelectedResponseMode();
            const {
                verificationStatus,
                toolsStatus,
                visionStatus,
                imageStatus,
                pdfStatus,
                audioStatus,
                imageOutputStatus,
                audioOutputStatus,
                agentRequiresTools
            } = state;
            const kinds = Array.from(
                new Set(this.composerAttachments.map((attachment) => attachment.kind))
            );
            const imageUnsupported =
                kinds.includes('image') &&
                (
                    imageStatus === 'unsupported' ||
                    visionStatus === 'fail' ||
                    visionStatus === 'unsupported'
                );
            const pdfUnsupported =
                kinds.includes('pdf') && pdfStatus === 'unsupported';
            const audioUnsupported =
                kinds.includes('audio') && audioStatus === 'unsupported';
            const imageOutputUnsupported =
                responseMode === 'image' &&
                imageOutputStatus === 'unsupported';
            const audioOutputUnsupported =
                responseMode === 'audio' &&
                audioOutputStatus === 'unsupported';

            if (
                (toolsStatus === 'fail' || toolsStatus === 'unsupported') &&
                agentRequiresTools
            ) {
                note.className = 'alert alert-danger py-2 px-3 small mt-2';
                note.textContent = gettext(
                    'This agent depends on tools or sub-agents, but the selected provider/model was explicitly verified without tool support.'
                );
                return;
            }

            if (
                imageUnsupported ||
                pdfUnsupported ||
                audioUnsupported ||
                imageOutputUnsupported ||
                audioOutputUnsupported
            ) {
                note.className = 'alert alert-danger py-2 px-3 small mt-2';
                if (imageOutputUnsupported) {
                    note.textContent = gettext(
                        'The selected agent provider was explicitly verified without image output support.'
                    );
                } else if (audioOutputUnsupported) {
                    note.textContent = gettext(
                        'The selected agent provider was explicitly verified without audio output support.'
                    );
                } else {
                    note.textContent = gettext(
                        'The selected agent provider was explicitly verified without support for one of the attached input types. Sending this message will be rejected.'
                    );
                }
                return;
            }

            if (!hasAttachments && (responseMode === 'text' || responseMode === 'auto')) {
                note.className = 'alert py-2 px-3 small mt-2 d-none';
                note.textContent = '';
                return;
            }

            if (verificationStatus === 'untested') {
                note.className = 'alert alert-warning py-2 px-3 small mt-2';
                note.textContent = gettext(
                    'The selected agent provider has not been actively verified for these attachment or output types yet.'
                );
                return;
            }

            if (verificationStatus === 'stale') {
                note.className = 'alert alert-warning py-2 px-3 small mt-2';
                note.textContent = gettext(
                    'The selected agent provider changed since its last verification. These attachments are allowed, but compatibility is no longer confirmed.'
                );
                return;
            }

            note.className = 'alert py-2 px-3 small mt-2 d-none';
            note.textContent = '';
        },

        getComposerAttachmentKind(file) {
            const fileType = `${file?.type || ''}`.toLowerCase();
            const fileName = `${file?.name || ''}`.toLowerCase();
            if (fileType.startsWith('image/')) return 'image';
            if (fileType === 'application/pdf' || fileName.endsWith('.pdf')) {
                return 'pdf';
            }
            if (fileType.startsWith('audio/')) return 'audio';
            return '';
        },

        getComposerAttachmentMaxBytes(kind) {
            if (kind === 'pdf') return this.maxComposerDocumentBytes;
            if (kind === 'audio') return this.maxComposerAudioBytes;
            return this.maxComposerImageBytes;
        },

        getComposerAttachmentTypeLabel(kind) {
            if (kind === 'pdf') return gettext('PDF');
            if (kind === 'audio') return gettext('audio');
            return gettext('image');
        },

        async publishArtifact(button) {
            const artifactId = `${button?.dataset?.artifactId || ''}`.trim();
            const urlTemplate = window.NovaApp?.urls?.artifactPublish;
            if (!artifactId || !urlTemplate) {
                this.showToast(
                    gettext('Artifact publishing is not configured on this page.'),
                    'warning'
                );
                return;
            }
            if (button.disabled) return;

            const originalHtml = button.innerHTML;
            button.disabled = true;
            button.innerHTML = gettext('Adding…');

            try {
                const response = await window.DOMUtils.csrfFetch(
                    urlTemplate.replace('0', artifactId),
                    { method: 'POST' }
                );
                const isJsonResponse = (response.headers.get('content-type') || '').includes(
                    'application/json'
                );
                const data = isJsonResponse ? await response.json() : null;
                if (!response.ok || !data?.success) {
                    throw new Error(
                        data?.error ||
                            data?.message ||
                            `Request failed (${response.status})`
                    );
                }

                this.markArtifactAsPublished(button);
                this.showToast(
                    data?.already_published
                        ? gettext('Artifact was already available in Files.')
                        : gettext('Artifact added to Files.'),
                    'success'
                );

                document.dispatchEvent(
                    new CustomEvent('threadChanged', {
                        detail: { threadId: data?.thread_id || this.currentThreadId || null }
                    })
                );
            } catch (error) {
                console.error('Error publishing artifact:', error);
                button.disabled = false;
                button.innerHTML = originalHtml;
                this.showToast(
                    error?.message || gettext('Failed to add artifact to Files.'),
                    'danger'
                );
            }
        },

        markArtifactAsPublished(button) {
            const item = button.closest('.artifact-summary-item');
            if (item) {
                item.dataset.publishedToFile = 'true';
            }
            button.disabled = false;
            button.classList.add(
                'artifact-publish-btn-published',
                'text-success'
            );
            button.textContent = gettext('Added to Files');
        },
    };
})();
