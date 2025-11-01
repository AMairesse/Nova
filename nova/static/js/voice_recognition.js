/* nova/static/js/voice_recognition.js - Voice recognition functionality */
(function () {
    'use strict';

    // Voice Recognition Manager Class
    class VoiceRecognitionManager {
        constructor() {
            this.recognition = null;
            this.isListening = false;
            this.isSupported = this.checkBrowserSupport();
            this.currentTranscript = '';
            this.finalTranscript = '';
            this.onResultCallback = null;
            this.onErrorCallback = null;
            this.onStartCallback = null;
            this.onEndCallback = null;

            if (this.isSupported) {
                this.initRecognition();
            }
        }

        // Check if browser supports Web Speech API
        checkBrowserSupport() {
            return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
        }

        // Initialize speech recognition
        initRecognition() {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            this.recognition = new SpeechRecognition();

            // Configure recognition settings
            this.recognition.continuous = false;
            this.recognition.interimResults = true;
            this.recognition.lang = this.getPreferredLanguage();

            // Set up event handlers
            this.recognition.onstart = () => {
                this.isListening = true;
                console.log('Voice recognition started');
                if (this.onStartCallback) this.onStartCallback();
            };

            this.recognition.onresult = (event) => {
                this.currentTranscript = '';
                this.finalTranscript = '';

                for (let i = event.resultIndex; i < event.results.length; i++) {
                    const transcript = event.results[i][0].transcript;
                    if (event.results[i].isFinal) {
                        this.finalTranscript += transcript;
                    } else {
                        this.currentTranscript += transcript;
                    }
                }

                // Call result callback with current transcript
                if (this.onResultCallback) {
                    this.onResultCallback(this.finalTranscript || this.currentTranscript, !this.finalTranscript);
                }
            };

            this.recognition.onerror = (event) => {
                console.error('Speech recognition error:', event.error);
                this.isListening = false;
                if (this.onErrorCallback) this.onErrorCallback(event.error);
                if (this.onEndCallback) this.onEndCallback();
            };

            this.recognition.onend = () => {
                this.isListening = false;
                console.log('Voice recognition ended');
                if (this.onEndCallback) this.onEndCallback();
            };
        }

        // Get preferred language based on browser/user settings
        getPreferredLanguage() {
            // Try to get language from various sources
            const lang = navigator.language || navigator.userLanguage || 'en-US';

            // Map common languages to speech recognition supported languages
            const langMap = {
                'fr': 'fr-FR',
                'en': 'en-US',
                'es': 'es-ES',
                'de': 'de-DE',
                'it': 'it-IT',
                'pt': 'pt-BR',
                'ja': 'ja-JP',
                'ko': 'ko-KR',
                'zh': 'zh-CN'
            };

            // Return mapped language or default to English
            return langMap[lang.split('-')[0]] || lang;
        }

        // Start voice recognition
        async start() {
            if (!this.isSupported) {
                throw new Error('Speech recognition not supported in this browser');
            }

            if (this.isListening) {
                console.warn('Already listening');
                return;
            }

            try {
                // Request microphone permission if needed
                if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
                    await navigator.mediaDevices.getUserMedia({ audio: true });
                }

                this.recognition.start();
            } catch (error) {
                console.error('Error starting voice recognition:', error);
                throw error;
            }
        }

        // Stop voice recognition
        stop() {
            if (this.recognition && this.isListening) {
                this.recognition.stop();
            }
        }

        // Abort voice recognition
        abort() {
            if (this.recognition && this.isListening) {
                this.recognition.abort();
            }
        }

        // Set callback functions
        onResult(callback) {
            this.onResultCallback = callback;
        }

        onError(callback) {
            this.onErrorCallback = callback;
        }

        onStart(callback) {
            this.onStartCallback = callback;
        }

        onEnd(callback) {
            this.onEndCallback = callback;
        }

        // Get current status
        getStatus() {
            return {
                isSupported: this.isSupported,
                isListening: this.isListening,
                currentTranscript: this.currentTranscript,
                finalTranscript: this.finalTranscript
            };
        }
    }

    // Create global instance
    window.VoiceRecognitionManager = VoiceRecognitionManager;

})();
