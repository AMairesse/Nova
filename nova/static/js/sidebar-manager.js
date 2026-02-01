// static/nova/js/sidebar-manager.js
// Simplified version using Bootstrap native tabs
(function () {
    'use strict';

    window.SidebarManager = {
        // Load sidebar content dynamically
        async loadSidebarContent() {
            if (window.FileManager.sidebarContentLoaded) return;

            const contentEl = document.getElementById('file-sidebar-content');
            if (!contentEl) {
                console.error('Sidebar content element not found');
                return;
            }

            try {
                const response = await fetch('/files/sidebar-panel/');
                if (!response.ok) throw new Error('Failed to load sidebar content');
                const html = await response.text();
                contentEl.innerHTML = html;

                // Bootstrap handles tabs natively via data-bs-toggle="tab"
                // We just need to bind event listeners for persistence and lazy loading
                this.bindTabEvents();
                window.FileManager.attachSidebarEventHandlers();

                // Restore saved tab for current thread
                this.restoreSavedTab();

                window.FileManager.sidebarContentLoaded = true;
            } catch (error) {
                console.error('Error loading sidebar:', error);
                contentEl.innerHTML = '<p class="alert alert-danger">Error loading files panel.</p>';
            }
        },

        // Bind Bootstrap tab events for persistence and lazy loading
        bindTabEvents() {
            const tabsEl = document.getElementById('files-webapps-tabs');
            if (!tabsEl) return;

            // Listen for Bootstrap tab shown events
            tabsEl.addEventListener('shown.bs.tab', (e) => {
                const target = e.target.getAttribute('data-bs-target');
                const tabName = target === '#pane-webapps' ? 'webapps' : 'files';

                // No persistence of the selected tab.

                // Lazy load webapps on first show
                if (tabName === 'webapps' && typeof window.WebappIntegration?.loadWebappsList === 'function') {
                    window.WebappIntegration.loadWebappsList();
                }
            });
        },

        // Restore the saved tab using Bootstrap Tab API
        restoreSavedTab() {
            // No persistence of the selected tab.
        },

        // Load file tree
        async loadTree() {
            const treeContainer = document.getElementById('file-tree-container');
            if (!treeContainer) {
                console.error('Tree container not found');
                return;
            }

            if (!window.FileManager.currentThreadId) {
                treeContainer.innerHTML = '<p class="text-muted">No thread selected</p>';
                return;
            }

            try {
                const response = await window.DOMUtils.csrfFetch(`/files/list/${window.FileManager.currentThreadId}/`);
                const data = await response.json();

                if (data.files) {
                    treeContainer.innerHTML = window.FileOperations.renderTree(data.files);
                } else {
                    treeContainer.innerHTML = '<p class="text-danger">Error loading files</p>';
                }
            } catch (error) {
                console.error('Error loading file tree:', error);
                treeContainer.innerHTML = '<p class="text-danger">Error loading files</p>';
            }
        },

        // Handle thread changes for sidebar
        async updateForThread(threadId) {
            window.FileManager.currentThreadId = threadId;

            const filesColumn = document.getElementById('files-sidebar');
            if (!filesColumn || filesColumn.classList.contains('files-hidden')) {
                if (window.WebSocketManager?.ws) {
                    window.WebSocketManager.ws.close();
                    window.WebSocketManager.ws = null;
                }
                return;
            }

            await this.loadSidebarContent();

            if (window.WebSocketManager?.ws) {
                window.WebSocketManager.ws.close();
                window.WebSocketManager.ws = null;
            }

            await this.loadTree();

            // Restore saved tab for this thread using Bootstrap Tab API
            this.restoreSavedTab();

            // If the Webapps tab is currently visible, refresh its list for the new thread.
            // (shown.bs.tab won't fire if the tab is already active.)
            const webappsTabEl = document.getElementById('tab-webapps');
            const isWebappsActive = Boolean(webappsTabEl && webappsTabEl.classList.contains('active'));
            if (isWebappsActive && typeof window.WebappIntegration?.loadWebappsList === 'function') {
                await window.WebappIntegration.loadWebappsList();
            }

            // Mobile: if user is currently on the Webapps view in the offcanvas, refresh it too.
            const mobileWebappsTab = document.getElementById('mobile-tab-webapps');
            const isMobileWebappsActive = Boolean(mobileWebappsTab && mobileWebappsTab.classList.contains('active'));
            if (isMobileWebappsActive && typeof window.WebappIntegration?.loadMobileWebappsList === 'function') {
                await window.WebappIntegration.loadMobileWebappsList();
            }

            if (window.ResponsiveManager) {
                window.ResponsiveManager.syncFilesContent();
            }

            if (window.WebSocketManager) {
                window.WebSocketManager.connectWebSocket();
            }
        }
    };

})();
