// static/nova/js/sidebar-manager.js
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

                // Bind tabs and file actions after content injection
                this.bindSidebarTabs();
                window.FileManager.attachSidebarEventHandlers();

                // Apply saved tab for current thread
                const threadId = window.FileManager.currentThreadId || window.StorageUtils.getThreadId() || null;
                const savedTab = threadId ? window.StorageUtils.getItem(window.StorageUtils.getSidebarTabKey(threadId), 'files') : 'files';
                this.applySidebarTabState(threadId, savedTab);

                window.FileManager.sidebarContentLoaded = true;
            } catch (error) {
                console.error('Error loading sidebar:', error);
                contentEl.innerHTML = '<p class="alert alert-danger">Error loading files panel.</p>';
            }
        },

        // Bind Files | Webapps tabs inside the sidebar
        bindSidebarTabs() {
            const tabsEl = document.getElementById('files-webapps-tabs');
            if (!tabsEl) return;

            const filesEl = document.getElementById('file-tree-container');
            const appsEl = document.getElementById('webapps-list-container');
            const toolbar = document.getElementById('files-toolbar');

            // Determine persisted tab per thread
            const threadId = window.FileManager.currentThreadId || window.StorageUtils.getThreadId() || null;
            const savedTab = threadId ? window.StorageUtils.getItem(window.StorageUtils.getSidebarTabKey(threadId), 'files') : 'files';

            // Initialize state from storage
            this.applySidebarTabState(threadId, savedTab);

            // Delegate click inside tabs header
            tabsEl.addEventListener('click', (e) => {
                const btn = e.target.closest('.nav-link');
                if (!btn) return;
                const target = btn.getAttribute('data-tab-target');
                if (!target) return;

                // Persist selection per thread
                const tid = window.FileManager.currentThreadId || window.StorageUtils.getThreadId() || null;
                if (tid) window.StorageUtils.setItem(window.StorageUtils.getSidebarTabKey(tid), target);

                // Apply state (active class + containers + toolbar)
                this.applySidebarTabState(tid, target);
            });
        },

        // Apply active tab and containers visibility based on provided tab ('files' | 'webapps')
        applySidebarTabState(threadId, tab) {
            const tabsEl = document.getElementById('files-webapps-tabs');
            const filesEl = document.getElementById('file-tree-container');
            const appsEl = document.getElementById('webapps-list-container');
            const toolbar = document.getElementById('files-toolbar');

            if (!tabsEl) return;

            const isWebapps = tab === 'webapps';

            // Active state on header buttons
            tabsEl.querySelectorAll('.nav-link').forEach(el => el.classList.remove('active'));
            const activeBtn = tabsEl.querySelector(`.nav-link[data-tab-target="${isWebapps ? 'webapps' : 'files'}"]`);
            if (activeBtn) activeBtn.classList.add('active');

            // Toggle containers
            if (filesEl) filesEl.classList.toggle('d-none', isWebapps);
            if (appsEl) {
                appsEl.classList.toggle('d-none', !isWebapps);
                appsEl.setAttribute('aria-hidden', isWebapps ? 'false' : 'true');
            }

            // Toggle toolbar (only for Files)
            if (toolbar) toolbar.classList.toggle('d-none', isWebapps);

            // Load webapps list on first show
            if (isWebapps && typeof window.WebappIntegration.loadWebappsList === 'function') {
                window.WebappIntegration.loadWebappsList();
            }
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
                if (window.WebSocketManager && window.WebSocketManager.ws) {
                    window.WebSocketManager.ws.close();
                    window.WebSocketManager.ws = null;
                }
                return;
            }

            await this.loadSidebarContent();

            if (window.WebSocketManager && window.WebSocketManager.ws) {
                window.WebSocketManager.ws.close();
                window.WebSocketManager.ws = null;
            }

            await this.loadTree();

            // Re-apply saved tab for this thread
            const tid = window.FileManager.currentThreadId || window.StorageUtils.getThreadId() || null;
            const savedTab = tid ? window.StorageUtils.getItem(window.StorageUtils.getSidebarTabKey(tid), 'files') : 'files';
            this.applySidebarTabState(tid, savedTab);

            if (window.ResponsiveManager) {
                window.ResponsiveManager.syncFilesContent();
            }

            if (window.WebSocketManager) {
                window.WebSocketManager.connectWebSocket();
            }
        },

        // Handle thread deletion
        handleThreadDeletion() {
            window.FileManager.currentThreadId = null;

            const filesColumn = document.getElementById('files-sidebar');
            if (!filesColumn || filesColumn.classList.contains('files-hidden')) {
                return;
            }

            if (!window.FileManager.sidebarContentLoaded) {
                return;
            }

            if (window.WebSocketManager && window.WebSocketManager.ws) {
                window.WebSocketManager.ws.close();
                window.WebSocketManager.ws = null;
            }

            const treeContainer = document.getElementById('file-tree-container');
            if (treeContainer) {
                treeContainer.innerHTML = '<p class="text-muted p-3">No thread selected</p>';
            }
        }
    };

})();