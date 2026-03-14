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
            if (!msg && !hasAttachments) return;
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
            }

            try {
                const formData = new FormData(form);
                formData.set('new_message', originalMessage);
                for (const attachment of this.composerAttachments) {
                    formData.append('message_attachments', attachment.file, attachment.file.name);
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
            textarea.style.height = 'auto';
            textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
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
            this.composerAttachmentSizeLabel =
                sizeLabel || this.formatAttachmentSizeLabel(this.maxComposerImageBytes);
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
