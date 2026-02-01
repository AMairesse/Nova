// static/nova/js/files.js
(function () {
  'use strict';

  /**
   * FileManager is a small facade that:
   * - Tracks the current thread id for file/webapp operations.
   * - Wires delegated click handlers for file actions (download/delete).
   * - Delegates actual work to SidebarManager, WebSocketManager, WebappIntegration.
   *
   * Initialization is invoked once from NovaApp.bootstrapThreadUI().
   */
  window.FileManager = {
    currentThreadId: null,
    isUploading: false,
    sidebarContentLoaded: false,
    _delegatesBound: false,
    _initialized: false,

    /**
     * Initialize FileManager once.
     * Safe to call multiple times; guarded by _initialized flag.
     */
    init() {
      if (this._initialized) return;
      this._initialized = true;

      this.attachSidebarEventHandlers();
      this.initDelegatedHandlers();

      // React to canonical threadChanged events to keep in sync.
      document.addEventListener('threadChanged', (e) => {
        const tid = e.detail?.threadId || null;
        this.updateForThread(tid);
      });

      // Initial sync happens when MessageManager dispatches `threadChanged`.
    },

    attachSidebarEventHandlers() {
      // Desktop upload buttons
      const desktopUploadBtn = document.getElementById('upload-files-btn');
      if (desktopUploadBtn && !desktopUploadBtn._novaBoundUploadFiles) {
        desktopUploadBtn._novaBoundUploadFiles = true;
        desktopUploadBtn.addEventListener('click', () => {
          const fileInput = document.getElementById('file-input');
          if (fileInput) fileInput.click();
        });
      }

      const desktopUploadDirectoryBtn = document.getElementById('upload-directory-btn');
      if (desktopUploadDirectoryBtn && !desktopUploadDirectoryBtn._novaBoundUploadDir) {
        desktopUploadDirectoryBtn._novaBoundUploadDir = true;
        desktopUploadDirectoryBtn.addEventListener('click', () => {
          const directoryInput = document.getElementById('directory-input');
          if (directoryInput) directoryInput.click();
        });
      }

      // Mobile upload buttons (offcanvas header)
      const mobileUploadBtn = document.getElementById('upload-files-btn-mobile');
      if (mobileUploadBtn && !mobileUploadBtn._novaBoundUploadFiles) {
        mobileUploadBtn._novaBoundUploadFiles = true;
        mobileUploadBtn.addEventListener('click', (e) => {
          e.preventDefault();
          const fileInput = document.getElementById('file-input');
          if (fileInput) fileInput.click();
        });
      }

      const mobileUploadDirectoryBtn = document.getElementById('upload-directory-btn-mobile');
      if (mobileUploadDirectoryBtn && !mobileUploadDirectoryBtn._novaBoundUploadDir) {
        mobileUploadDirectoryBtn._novaBoundUploadDir = true;
        mobileUploadDirectoryBtn.addEventListener('click', (e) => {
          e.preventDefault();
          const directoryInput = document.getElementById('directory-input');
          if (directoryInput) directoryInput.click();
        });
      }

      // File input handlers (shared)
      const fileInput = document.getElementById('file-input');
      if (fileInput && !fileInput._novaBoundChange) {
        fileInput._novaBoundChange = true;
        fileInput.addEventListener('change', (e) => {
          window.FileOperations.handleFileUpload(e.target.files);
        });
      }

      const directoryInput = document.getElementById('directory-input');
      if (directoryInput && !directoryInput._novaBoundChange) {
        directoryInput._novaBoundChange = true;
        directoryInput.addEventListener('change', (e) => {
          window.FileOperations.handleDirectoryUpload(e.target.files);
        });
      }
    },

    // Delegated handlers so desktop and mobile clones both work
    initDelegatedHandlers() {
      if (this._delegatesBound) return;
      this._delegatesBound = true;

      document.addEventListener('click', (e) => {
        if (this._handleDownloadClick(e)) return;
        if (this._handleDeleteClick(e)) return;
        if (this._handleWebappPreviewClick(e)) return;
      });
    },

    // --- Internal delegated handlers ------------------------------------------------

    _handleDownloadClick(e) {
      const downloadEl = e.target.closest('a[data-action="download"], .file-download-link');
      if (!downloadEl) return false;

      e.preventDefault();
      const item = downloadEl.closest('.file-tree-item');
      if (!item) return true;

      const fileId = item.dataset.fileId;
      const fileName = item.dataset.fileName || (downloadEl.textContent || '').trim();
      if (!fileId) return true;

      window.FileOperations.downloadFile(fileId, fileName).catch((err) => {
        console.error('Download failed', err);
        alert('Download failed.');
      });
      return true;
    },

    _handleDeleteClick(e) {
      const deleteEl = e.target.closest('[data-action="delete"], .file-delete-btn');
      if (!deleteEl) return false;

      e.preventDefault();
      const item = deleteEl.closest('.file-tree-item');
      if (!item) return true;

      const itemType = item.dataset.type;
      const fileName =
        item.dataset.fileName ||
        (item.querySelector('.file-item-name, .file-download-link')?.textContent || '').trim();

      if (itemType === 'dir' || itemType === 'folder') {
        const dirPath = item.dataset.path || '';
        if (!confirm(`Are you sure you want to delete directory "${fileName}" and all files inside it?`)) {
          return true;
        }
        window.FileOperations.deleteDirectory(fileName, dirPath);
      } else {
        const fileId = item.dataset.fileId;
        if (!fileId) return true;
        window.FileOperations.deleteSingleFile(fileId, fileName);
      }
      return true;
    },

    _handleWebappPreviewClick(e) {
      const previewEl = e.target.closest('.webapp-preview-btn');
      if (!previewEl) return false;

      e.preventDefault();
      const slug = previewEl.dataset.slug || '';
      const threadId = this.currentThreadId;
      if (!slug || !threadId) return true;

      // Mobile: open dedicated full-page preview
      const isMobile = window.innerWidth < 992;
      if (isMobile) {
        window.location.href = `/apps/preview/${threadId}/${slug}/`;
        return true;
      }

      // Desktop: keep existing behavior (split preview handled by preview page/layout)
      window.location.href = `/apps/preview/${threadId}/${slug}/`;
      return true;
    },

    // --- Delegation helpers ---------------------------------------------------------

    loadSidebarContent() {
      return window.SidebarManager.loadSidebarContent();
    },

    loadTree() {
      return window.SidebarManager.loadTree();
    },

    connectWebSocket() {
      return window.WebSocketManager.connectWebSocket();
    },

    updateForThread(threadId) {
      if (!threadId) {
        this.currentThreadId = null;
        return;
      }
      this.currentThreadId = threadId;
      return window.SidebarManager.updateForThread(threadId);
    },

    loadWebappsList() {
      return window.WebappIntegration.loadWebappsList();
    },

    activateSplitPreview(slug, url) {
      return window.WebappIntegration.activateSplitPreview(slug, url);
    }
  };

})();
