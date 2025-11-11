// static/nova/js/voice-manager.js
(function () {
    'use strict';

    // ============================================================================
    // VOICE MANAGER - Handles voice recognition functionality
    // ============================================================================
    window.VoiceManager = class VoiceManager {
        constructor(messageManager) {
            this.messageManager = messageManager;
            this.voiceRecognition = null;
            this.init();
        }

        init() {
            if (typeof window.VoiceRecognitionManager !== 'undefined') {
                this.voiceRecognition = new window.VoiceRecognitionManager();

                // Set up voice recognition callbacks
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
            }
        }

        // Handle voice button click
        handleVoiceButtonClick() {
            if (!this.voiceRecognition) {
                console.error('Voice recognition not initialized');
                return;
            }

            const status = this.voiceRecognition.getStatus();

            if (!status.isSupported) {
                alert('Voice recognition is not supported in this browser. Please use a modern browser like Chrome, Edge, or Safari.');
                return;
            }

            if (status.isListening) {
                // Stop listening
                this.voiceRecognition.stop();
            } else {
                // Start listening
                try {
                    this.voiceRecognition.start();
                } catch (error) {
                    console.error('Error starting voice recognition:', error);
                    this.showVoiceError('Failed to start voice recognition. Please check microphone permissions.');
                }
            }
        }

        // Handle voice recognition result
        handleVoiceResult(transcript, isInterim) {
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (!textarea) return;

            // Update textarea with transcript
            textarea.value = transcript;

            // Trigger input event to update textarea height
            textarea.dispatchEvent(new Event('input', { bubbles: true }));

            // Focus the textarea
            textarea.focus();

            // If this is final result, enable send button
            if (!isInterim) {
                const sendBtn = document.getElementById('send-btn');
                if (sendBtn && !sendBtn.disabled) {
                    // Auto-submit if transcript is not empty
                    if (transcript.trim()) {
                        const form = document.getElementById('message-form');
                        if (form) {
                            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                        }
                    }
                }
            }
        }

        // Handle voice recognition error
        handleVoiceError(error) {
            console.error('Voice recognition error:', error);
            let message = 'Voice recognition error occurred.';

            switch (error) {
                case 'not-allowed':
                    message = 'Microphone access denied. Please allow microphone access and try again.';
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
        }

        // Handle voice recognition start
        handleVoiceStart() {
            this.updateVoiceButtonState(true);
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (textarea) {
                textarea.placeholder = 'Listening... Speak now';
            }
        }

        // Handle voice recognition end
        handleVoiceEnd() {
            this.updateVoiceButtonState(false);
            const textarea = document.querySelector('#message-container textarea[name="new_message"]');
            if (textarea) {
                textarea.placeholder = 'Type your message...';
            }
        }

        // Update voice button visual state
        updateVoiceButtonState(isListening = false) {
            const voiceBtn = document.getElementById('voice-btn');
            if (!voiceBtn) return;

            // Hide button if voice recognition is not supported
            if (this.voiceRecognition && !this.voiceRecognition.getStatus().isSupported) {
                voiceBtn.style.display = 'none';
                return;
            }

            // Show button if supported
            voiceBtn.style.display = '';

            const icon = voiceBtn.querySelector('i');
            if (!icon) return;

            if (isListening) {
                voiceBtn.classList.remove('btn-outline-secondary');
                voiceBtn.classList.add('btn-danger');
                icon.className = 'bi bi-mic-fill text-white';
                voiceBtn.title = 'Stop voice input';
            } else {
                voiceBtn.classList.remove('btn-danger');
                voiceBtn.classList.add('btn-outline-secondary');
                icon.className = 'bi bi-mic';
                voiceBtn.title = 'Voice input';
            }
        }

        // Show voice error message
        showVoiceError(message) {
            // For now, just log to console and show alert
            // In a production app, you might want to show a toast notification
            console.warn('Voice recognition:', message);
            alert(message);
        }
    };

})();