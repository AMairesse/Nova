// static/nova/js/files.js
(function () {
  'use strict';

  window.FileManager = {
    currentThreadId: window.StorageUtils.getThreadId(),
    isUploading: false,
    sidebarContentLoaded: false,  // Cache flag to avoid reloading content
    _delegatesBound: false,       // Ensure we bind delegated handlers only once

    attachSidebarEventHandlers() {
      const uploadBtn = document.getElementById('upload-files-btn');
      if (uploadBtn) {
        uploadBtn.addEventListener('click', () => {
          const fileInput = document.getElementById('file-input');
          if (fileInput) fileInput.click();
        });
      }

      const uploadDirectoryBtn = document.getElementById('upload-directory-btn');
      if (uploadDirectoryBtn) {
        uploadDirectoryBtn.addEventListener('click', () => {
          const directoryInput = document.getElementById('directory-input');
          if (directoryInput) directoryInput.click();
        });
      }

      const fileInput = document.getElementById('file-input');
      if (fileInput) {
        fileInput.addEventListener('change', (e) => {
          window.FileOperations.handleFileUpload(e.target.files);
        });
      }

      const directoryInput = document.getElementById('directory-input');
      if (directoryInput) {
        directoryInput.addEventListener('change', (e) => {
          window.FileOperations.handleDirectoryUpload(e.target.files);
        });
      }
    },

    // Delegated handlers so desktop and mobile clones both work
    initDelegatedHandlers() {
      if (this._delegatesBound) return;
      this._delegatesBound = true;

      document.addEventListener('click', async (e) => {
        // Download
        const downloadEl = e.target.closest('a[data-action="download"], .file-download-link');
        if (downloadEl) {
          e.preventDefault();
          const item = downloadEl.closest('.file-tree-item');
          if (!item) return;

          const fileId = item.dataset.fileId;
          const fileName = item.dataset.fileName || (downloadEl.textContent || '').trim();
          if (!fileId) return;

          try {
            await window.FileOperations.downloadFile(fileId, fileName);
          } catch (err) {
            console.error('Download failed', err);
            alert('Download failed.');
          }
          return;
        }

        // Delete
        const deleteEl = e.target.closest('[data-action="delete"], .file-delete-btn');
        if (deleteEl) {
          e.preventDefault();
          const item = deleteEl.closest('.file-tree-item');
          if (!item) return;

          const itemType = item.dataset.type;
          const fileName = item.dataset.fileName || (item.querySelector('.file-item-name, .file-download-link')?.textContent || '').trim();

          if (itemType === 'dir' || itemType === 'folder') {
            const dirPath = item.dataset.path || '';
            if (!confirm(`Are you sure you want to delete directory "${fileName}" and all files inside it?`)) return;
            await window.FileOperations.deleteDirectory(fileName, dirPath);
          } else {
            const fileId = item.dataset.fileId;
            if (!fileId) return;
            await window.FileOperations.deleteSingleFile(fileId, fileName);
          }
          return;
        }

        // Webapps: open dedicated preview page
        const previewEl = e.target.closest('.webapp-preview-btn');
        if (previewEl) {
          e.preventDefault();
          const slug = previewEl.dataset.slug || '';
          const threadId = this.currentThreadId || window.StorageUtils.getThreadId();
          if (!slug || !threadId) return;
          window.location.href = `/apps/preview/${threadId}/${slug}/`;
          return;
        }
      });
    },

    // Delegate to SidebarManager
    loadSidebarContent() {
      return window.SidebarManager.loadSidebarContent();
    },

    loadTree() {
      return window.SidebarManager.loadTree();
    },

    // Delegate to WebSocketManager
    connectWebSocket() {
      return window.WebSocketManager.connectWebSocket();
    },

    // Delegate to SidebarManager
    updateForThread(threadId) {
      return window.SidebarManager.updateForThread(threadId);
    },

    handleThreadDeletion() {
      return window.SidebarManager.handleThreadDeletion();
    },

    // Delegate to WebappIntegration
    loadWebappsList() {
      return window.WebappIntegration.loadWebappsList();
    },

    activateSplitPreview(slug, url) {
      return window.WebappIntegration.activateSplitPreview(slug, url);
    }
  };

  // Initialize delegated handlers and thread sync once DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    if (window.FileManager && typeof window.FileManager.initDelegatedHandlers === 'function') {
      window.FileManager.initDelegatedHandlers();
    }

    // Sync sidebar content with current thread whenever it changes
    document.addEventListener('threadChanged', (e) => {
      const tid = e.detail?.threadId || null;
      if (window.FileManager && typeof window.FileManager.updateForThread === 'function') {
        window.FileManager.updateForThread(tid);
      }
    });

    // Initialize sidebar for last thread on load (if available)
    const initialTid = window.StorageUtils.getThreadId();
    if (initialTid && window.FileManager && typeof window.FileManager.updateForThread === 'function') {
      window.FileManager.updateForThread(initialTid);
    }
  });

})();
