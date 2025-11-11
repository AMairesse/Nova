// static/nova/js/websocket-manager.js
(function () {
    'use strict';

    window.WebSocketManager = {
        ws: null,

        // Connect WebSocket for file operations
        connectWebSocket() {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

            if (!window.FileManager.currentThreadId) return;

            const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${location.host}/ws/files/${window.FileManager.currentThreadId}/`;

            this.ws = new WebSocket(wsUrl);

            this.ws.onmessage = (e) => {
                try {
                    const data = JSON.parse(e.data);
                    console.log('WS received:', data);

                    if (data.type === 'file_update') {
                        window.SidebarManager.loadTree();
                    } else if (data.type === 'progress') {
                        console.log(`Progress update: ${data.progress}%`);
                        const progressBar = document.querySelector('#upload-progress .progress-bar');
                        if (progressBar && window.FileManager.isUploading) {
                            progressBar.style.width = `${data.progress}%`;
                            progressBar.textContent = `${data.progress}%`;
                            progressBar.setAttribute('aria-valuenow', data.progress);
                            if (data.progress === 100) {
                                progressBar.classList.add('bg-success');
                            }
                        }
                    }
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };

            this.ws.onerror = (error) => {
                console.error('WebSocket error:', error);
            };
        }
    };

})();