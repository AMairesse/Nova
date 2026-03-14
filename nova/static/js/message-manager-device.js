(function () {
    'use strict';

    window.NovaApp = window.NovaApp || {};
    window.NovaApp.Modules = window.NovaApp.Modules || {};

    window.NovaApp.Modules.MessageDeviceMethods = {
        initVoiceRecognition() {
            if (typeof window.VoiceRecognitionManager === 'undefined') {
                return;
            }

            this.voiceRecognition = new window.VoiceRecognitionManager();

            this.voiceRecognition.onResult((transcript, isInterim) => {
                this.handleVoiceResult(transcript, isInterim);
            });

            this.voiceRecognition.onError((error) => {
                this.handleVoiceError(error);
            });

            this.voiceRecognition.onStart(() => {
                this.handleVoiceStart();
            });

            this.voiceRecognition.onEnd(() => {
                this.handleVoiceEnd();
            });
        },

        handleVoiceButtonClick() {
            if (!this.voiceRecognition) {
                console.error('Voice recognition not initialized');
                return;
            }

            const status = this.voiceRecognition.getStatus();

            if (!status.isSupported) {
                alert(
                    'Voice recognition is not supported in this browser. Please use a modern browser like Chrome, Edge, or Safari.'
                );
                return;
            }

            if (status.isListening) {
                this.voiceRecognition.stop();
                return;
            }

            try {
                this.voiceRecognition.start();
            } catch (error) {
                console.error('Error starting voice recognition:', error);
                this.showVoiceError(
                    'Failed to start voice recognition. Please check microphone permissions.'
                );
            }
        },

        handleVoiceResult(transcript, isInterim) {
            const textarea = document.querySelector(
                '#message-container textarea[name="new_message"]'
            );
            if (!textarea) return;

            textarea.value = transcript;
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.focus();

            if (isInterim) {
                return;
            }

            const sendBtn = document.getElementById('send-btn');
            if (sendBtn && !sendBtn.disabled && transcript.trim()) {
                const form = document.getElementById('message-form');
                if (form) {
                    this.triggerComposerSubmit(form);
                }
            }
        },

        handleVoiceError(error) {
            console.error('Voice recognition error:', error);
            let message = 'Voice recognition error occurred.';

            switch (error) {
                case 'not-allowed':
                    message =
                        'Microphone access denied. Please allow microphone access and try again.';
                    break;
                case 'no-speech':
                    message = 'No speech detected. Please try speaking again.';
                    break;
                case 'audio-capture':
                    message = 'Audio capture failed. Please check your microphone.';
                    break;
                case 'network':
                    message = 'Network error during voice recognition.';
                    break;
                default:
                    message = `Voice recognition error: ${error}`;
            }

            this.showVoiceError(message);
        },

        handleVoiceStart() {
            this.updateVoiceButtonState(true);
            const textarea = document.querySelector(
                '#message-container textarea[name="new_message"]'
            );
            if (textarea) {
                textarea.placeholder = 'Listening... Speak now';
            }
        },

        handleVoiceEnd() {
            this.updateVoiceButtonState(false);
            const textarea = document.querySelector(
                '#message-container textarea[name="new_message"]'
            );
            if (textarea) {
                textarea.placeholder = 'Type your message...';
            }
        },

        updateVoiceButtonState(isListening = false) {
            const voiceBtn = document.getElementById('voice-btn');
            if (!voiceBtn) return;

            if (
                this.voiceRecognition &&
                !this.voiceRecognition.getStatus().isSupported
            ) {
                voiceBtn.style.display = 'none';
                return;
            }

            voiceBtn.style.display = '';

            const icon = voiceBtn.querySelector('i');
            if (!icon) return;

            if (isListening) {
                voiceBtn.classList.remove('btn-outline-secondary');
                voiceBtn.classList.add('btn-danger');
                icon.className = 'bi bi-mic-fill text-white';
                voiceBtn.title = 'Stop voice input';
                return;
            }

            voiceBtn.classList.remove('btn-danger');
            voiceBtn.classList.add('btn-outline-secondary');
            icon.className = 'bi bi-mic';
            voiceBtn.title = 'Voice input';
        },

        showVoiceError(message) {
            console.warn('Voice recognition:', message);
            alert(message);
        },

        initLongPressContextMenu() {
            if (!('ontouchstart' in window)) return;

            const conversationContainer = document.getElementById(
                'conversation-container'
            );
            if (!conversationContainer) return;

            const offcanvasEl = document.getElementById('messageContextMenu');
            if (offcanvasEl) {
                this.contextMenuOffcanvas = new bootstrap.Offcanvas(offcanvasEl);
            }

            conversationContainer.addEventListener(
                'touchstart',
                (e) => this.handleTouchStart(e),
                { passive: true }
            );
            conversationContainer.addEventListener(
                'touchend',
                (e) => this.handleTouchEnd(e)
            );
            conversationContainer.addEventListener(
                'touchmove',
                (e) => this.handleTouchMove(e),
                { passive: true }
            );
            conversationContainer.addEventListener(
                'touchcancel',
                (e) => this.handleTouchCancel(e)
            );

            this.initContextMenuActions();
        },

        handleTouchStart(e) {
            const messageCard = e.target.closest('.message .card');
            if (!messageCard) return;

            const touch = e.touches[0];
            this.touchStartPos = { x: touch.clientX, y: touch.clientY };
            this.longPressTarget = messageCard;

            const messageEl = messageCard.closest('.message');

            this.longPressTimer = setTimeout(() => {
                if (messageEl) messageEl.classList.add('long-press-active');

                if (window.navigator && window.navigator.vibrate) {
                    window.navigator.vibrate(50);
                }

                setTimeout(() => {
                    if (messageEl) messageEl.classList.remove('long-press-active');
                    this.showMessageContextMenu(messageCard);
                }, 100);
            }, this.longPressDuration);
        },

        handleTouchEnd() {
            this.cancelLongPress();
        },

        handleTouchMove(e) {
            if (!this.touchStartPos) return;

            const touch = e.touches[0];
            const dx = Math.abs(touch.clientX - this.touchStartPos.x);
            const dy = Math.abs(touch.clientY - this.touchStartPos.y);

            if (dx > 10 || dy > 10) {
                this.cancelLongPress();
            }
        },

        handleTouchCancel() {
            this.cancelLongPress();
        },

        cancelLongPress() {
            if (this.longPressTimer) {
                clearTimeout(this.longPressTimer);
                this.longPressTimer = null;
            }

            const activeEl = document.querySelector('.message.long-press-active');
            if (activeEl) activeEl.classList.remove('long-press-active');

            this.touchStartPos = null;
        },

        showMessageContextMenu(messageCard) {
            if (!this.contextMenuOffcanvas) return;

            const messageEl = messageCard.closest('.message');
            if (!messageEl) return;

            this.currentContextMessage = messageEl;

            const cardBody = messageCard.querySelector('.card-body');
            this.currentMessageText = cardBody ? cardBody.textContent.trim() : '';

            const contextInfo = document.getElementById('context-menu-info');
            const tokensEl = document.getElementById('context-menu-tokens');
            const cardFooter = messageCard.querySelector('.card-footer-consumption');

            if (cardFooter && cardFooter.textContent.trim()) {
                const footerText = cardFooter.textContent.trim();
                if (tokensEl) {
                    tokensEl.textContent = footerText
                        .replace('Context consumption:', '')
                        .trim();
                }
                if (contextInfo) contextInfo.classList.remove('d-none');
            } else if (contextInfo) {
                contextInfo.classList.add('d-none');
            }

            this.contextMenuOffcanvas.show();
        },

        initContextMenuActions() {
            const copyBtn = document.getElementById('context-menu-copy');
            if (copyBtn) {
                copyBtn.addEventListener('click', () => {
                    this.copyMessageToClipboard();
                    this.contextMenuOffcanvas.hide();
                });
            }
        },

        async copyMessageToClipboard() {
            if (!this.currentMessageText) return;

            try {
                await navigator.clipboard.writeText(this.currentMessageText);
                console.log('Message copied to clipboard');
            } catch (err) {
                console.error('Failed to copy message:', err);
                this.fallbackCopyToClipboard(this.currentMessageText);
            }
        },

        fallbackCopyToClipboard(text) {
            const textArea = document.createElement('textarea');
            textArea.value = text;
            textArea.style.position = 'fixed';
            textArea.style.left = '-9999px';
            document.body.appendChild(textArea);
            textArea.select();
            try {
                document.execCommand('copy');
            } catch (err) {
                console.error('Fallback copy failed:', err);
            }
            document.body.removeChild(textArea);
        },
    };
})();
