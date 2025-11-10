// static/nova/js/files.js
(function () {
  'use strict';

  window.FileManager = {
    currentThreadId: localStorage.getItem('lastThreadId') || null,
    ws: null,
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
          this.handleFileUpload(e.target.files);
        });
      }

      const directoryInput = document.getElementById('directory-input');
      if (directoryInput) {
        directoryInput.addEventListener('change', (e) => {
          this.handleDirectoryUpload(e.target.files);
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
            await this.downloadFile(fileId, fileName);
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
            await this.deleteDirectory(fileName, dirPath);
          } else {
            const fileId = item.dataset.fileId;
            if (!fileId) return;
            await this.deleteSingleFile(fileId, fileName);
          }
          return;
        }

        // Webapps: preview side-by-side
        const previewEl = e.target.closest('.webapp-preview-btn');
        if (previewEl) {
          e.preventDefault();
          const slug = previewEl.dataset.slug || '';
          const url = previewEl.dataset.url || '';
          if (window.FileManager && typeof window.FileManager.activateSplitPreview === 'function') {
            window.FileManager.activateSplitPreview(slug, url);
          }
          return;
        }
      });
    },

    // Bind Files | Webapps tabs inside the sidebar (works even when content is injected via innerHTML)
    bindSidebarTabs() {
      const tabsEl = document.getElementById('files-webapps-tabs');
      if (!tabsEl) return;

      const filesEl = document.getElementById('file-tree-container');
      const appsEl = document.getElementById('webapps-list-container');
      const toolbar = document.getElementById('files-toolbar');

      // Determine persisted tab per thread
      const threadId = this.currentThreadId || localStorage.getItem('lastThreadId') || null;
      const savedTab = threadId ? (localStorage.getItem(`sidebarTab:${threadId}`) || 'files') : 'files';

      // Initialize state from storage
      this.applySidebarTabState(threadId, savedTab);

      // Delegate click inside tabs header
      tabsEl.addEventListener('click', (e) => {
        const btn = e.target.closest('.nav-link');
        if (!btn) return;
        const target = btn.getAttribute('data-tab-target');
        if (!target) return;

        // Persist selection per thread
        const tid = this.currentThreadId || localStorage.getItem('lastThreadId') || null;
        if (tid) localStorage.setItem(`sidebarTab:${tid}`, target);

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
      if (isWebapps && typeof window.FileManager.loadWebappsList === 'function') {
        window.FileManager.loadWebappsList();
      }
    },

    async loadSidebarContent() {
      if (this.sidebarContentLoaded) return;

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
        this.attachSidebarEventHandlers();

        // Apply saved tab for current thread
        const threadId = this.currentThreadId || localStorage.getItem('lastThreadId') || null;
        const savedTab = threadId ? (localStorage.getItem(`sidebarTab:${threadId}`) || 'files') : 'files';
        this.applySidebarTabState(threadId, savedTab);

        this.sidebarContentLoaded = true;
      } catch (error) {
        console.error('Error loading sidebar:', error);
        contentEl.innerHTML = '<p class="alert alert-danger">Error loading files panel.</p>';
      }
    },

    async loadTree() {
      const treeContainer = document.getElementById('file-tree-container');
      if (!treeContainer) {
        console.error('Tree container not found');
        return;
      }

      if (!this.currentThreadId) {
        treeContainer.innerHTML = '<p class="text-muted">No thread selected</p>';
        return;
      }

      try {
        const response = await window.DOMUtils.csrfFetch(`/files/list/${this.currentThreadId}/`);
        const data = await response.json();

        if (data.files) {
          treeContainer.innerHTML = this.renderTree(data.files);
        } else {
          treeContainer.innerHTML = '<p class="text-danger">Error loading files</p>';
        }
      } catch (error) {
        console.error('Error loading file tree:', error);
        treeContainer.innerHTML = '<p class="text-danger">Error loading files</p>';
      }
    },

    renderTree(nodes, parentPath = '', level = 0) {
      if (!nodes || nodes.length === 0) {
        return level === 0 ? '<p class="text-muted p-3">' + gettext('No files in this thread.') + '</p>' : '';
      }

      let html = `<ul class="file-tree-list ${level === 0 ? 'root' : ''}" style="padding-left: ${level * 12}px;">`;

      nodes.forEach(node => {
        const isFolder = node.type === 'dir' || node.type === 'folder';
        const icon = isFolder ? 'bi-folder' : this.getFileIcon(node.mime);
        const nodeId = node.id || `temp-${Date.now()}-${Math.random()}`;
        const nodePath = node.full_path || node.path || '';
        const nodeName = node.name || '';

        if (isFolder) {
          html += `
            <li class="file-tree-item file-tree-folder"
                data-type="dir"
                data-path="${this._escapeAttr(nodePath)}"
                data-file-name="${this._escapeAttr(nodeName)}">
              <i class="bi ${icon} me-1"></i>
              <span class="file-item-name">${this._escapeHTML(nodeName)}</span>
              <button type="button"
                      class="file-delete-btn btn btn-link btn-sm p-0 ms-2 text-danger"
                      data-action="delete"
                      aria-label="Delete">
                <i class="bi bi-trash"></i>
              </button>
              ${node.children ? this.renderTree(node.children, nodePath, level + 1) : ''}
            </li>
          `;
        } else {
          html += `
            <li class="file-tree-item"
                data-file-id="${this._escapeAttr(nodeId)}"
                data-file-name="${this._escapeAttr(nodeName)}"
                data-type="file"
                data-path="${this._escapeAttr(nodePath)}">
              <i class="bi ${icon} me-1"></i>
              <a href="#" class="file-download-link" data-action="download">${this._escapeHTML(nodeName)}</a>
              <button type="button"
                      class="file-delete-btn btn btn-link btn-sm p-0 ms-2 text-danger"
                      data-action="delete"
                      aria-label="Delete">
                <i class="bi bi-trash"></i>
              </button>
            </li>
          `;
        }
      });

      html += '</ul>';
      return html;
    },

    _escapeHTML(text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    },

    _escapeAttr(text) {
      // Conservative attribute escaping
      return this._escapeHTML(text).replace(/`/g, '&#96;');
    },

    getFileIcon(mimeType) {
      if (!mimeType) return 'bi-file-earmark';

      const iconMap = {
        'application/pdf': 'bi-file-pdf',
        'image/': 'bi-file-image',
        'video/': 'bi-file-play',
        'audio/': 'bi-file-music',
        'text/': 'bi-file-text',
        'application/zip': 'bi-file-zip',
      };

      for (const [prefix, icon] of Object.entries(iconMap)) {
        if (mimeType.startsWith(prefix)) return icon;
      }

      return 'bi-file-earmark';
    },

    async downloadFile(fileId, fileName) {
      try {
        const response = await window.DOMUtils.csrfFetch(`/files/download-url/${fileId}/`);
        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(errorData.error || 'Failed to get download URL');
        }
        const data = await response.json();

        if (data.url) {
          const link = document.createElement('a');
          link.href = data.url;
          if (fileName) link.download = fileName;
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
        } else {
          throw new Error('No URL received');
        }
      } catch (error) {
        console.error('Download error:', error);
        alert(`Error downloading "${fileName}": ${error.message}`);
      }
    },

    // Helper function to collect all file IDs within a directory path
    collectFilesInDirectory(nodes, targetPath) {
      const fileIds = [];

      const traverse = (nodeList, currentPath = '') => {
        if (!nodeList) return;

        nodeList.forEach(node => {
          const nodePath = node.full_path || node.path || '';

          if (node.type === 'file' && node.id && nodePath.startsWith(targetPath)) {
            fileIds.push(node.id);
          }

          if (node.children) {
            traverse(node.children, nodePath);
          }
        });
      };

      traverse(nodes);
      return fileIds;
    },

    // Kept for backward compatibility; now uses data-file-id correctly
    async deleteFile(fileId) {
      const item = document.querySelector(`.file-tree-item[data-file-id="${CSS.escape(String(fileId))}"]`);
      if (!item) {
        console.error('File item not found');
        return;
      }

      const fileName = item.dataset.fileName || item.querySelector('.file-item-name, .file-download-link')?.textContent || '';
      const itemType = item.dataset.type;
      const itemPath = item.dataset.path;

      const isDirectory = itemType === 'dir' || itemType === 'folder';
      if (isDirectory) {
        await this.deleteDirectory(fileName, itemPath);
      } else {
        await this.deleteSingleFile(fileId, fileName);
      }
    },

    async deleteSingleFile(fileId, fileName) {
      if (!confirm(`Are you sure you want to delete "${fileName}"?`)) {
        return;
      }

      try {
        const response = await window.DOMUtils.csrfFetch(`/files/delete/${fileId}/`, { method: 'DELETE' });
        if (response.ok) {
          // Reload the file tree to reflect changes
          await this.loadTree();

          // Sync to mobile after tree update
          if (window.ResponsiveManager) {
            window.ResponsiveManager.syncFilesContent();
          }

        } else {
          const errorData = await response.json().catch(() => ({}));
          const errorMessage = errorData.error || 'Failed to delete file';
          alert(`Error deleting file: ${errorMessage}`);
          console.error('Delete failed:', errorMessage);
        }
      } catch (error) {
        console.error('Error deleting file:', error);
        alert('Error deleting file. Please try again.');
      }
    },

    async deleteDirectory(directoryName, directoryPath) {
      try {
        const response = await window.DOMUtils.csrfFetch(`/files/list/${this.currentThreadId}/`);
        const data = await response.json();

        if (!data.files) {
          alert('Error loading file list for directory deletion');
          return;
        }

        const fileIds = this.collectFilesInDirectory(data.files, directoryPath);

        if (fileIds.length === 0) {
          alert(`Directory "${directoryName}" appears to be empty or contains no files to delete.`);
          return;
        }

        // Show confirmation (already asked in delegated handler, but keep for direct calls)
        if (!confirm(`Are you sure you want to delete directory "${directoryName}" and all ${fileIds.length} files inside it?`)) {
          return;
        }

        let deletedCount = 0;
        let failedCount = 0;

        for (const fileId of fileIds) {
          try {
            const deleteResponse = await window.DOMUtils.csrfFetch(`/files/delete/${fileId}/`, { method: 'DELETE' });
            if (deleteResponse.ok) {
              deletedCount++;
            } else {
              failedCount++;
              console.error(`Failed to delete file with ID: ${fileId}`);
            }
          } catch (error) {
            failedCount++;
            console.error(`Error deleting file with ID ${fileId}:`, error);
          }
        }

        await this.loadTree();

        if (window.ResponsiveManager) {
          window.ResponsiveManager.syncFilesContent();
        }

        if (failedCount > 0) {
          alert(`Directory deletion completed with issues: ${deletedCount} files deleted, ${failedCount} files failed to delete.`);
        }

      } catch (error) {
        console.error('Error during directory deletion:', error);
        alert('Error deleting directory. Please try again.');
      }
    },

    // Factorized function for upload completion handling
    handleUploadCompletion(inputId, progressMessage = null, delay = 1000) {
      const input = document.getElementById(inputId);
      if (input) input.value = '';

      setTimeout(async () => {
        await this.loadTree();

        if (window.ResponsiveManager) {
          window.ResponsiveManager.syncFilesContent();
        }

        this.isUploading = false;
        const progressEl = document.getElementById('upload-progress');
        if (progressEl) {
          if (progressMessage) {
            const progressBar = document.querySelector('#upload-progress .progress-bar');
            if (progressBar) progressBar.textContent = progressMessage;
          }
          setTimeout(() => {
            progressEl.style.display = 'none';
          }, delay);
        }
      }, 500);
    },

    async handleFileUpload(files) {
      if (!files || files.length === 0) return;
      if (!this.currentThreadId) {
        alert('Please select a thread first');
        return;
      }

      const formData = new FormData();
      const fileData = [];

      for (const file of files) {
        formData.append('files', file);
        formData.append('paths', `/${file.name}`);
        fileData.push({
          name: file.name,
          size: file.size,
          type: file.type
        });
      }

      formData.append('file_data', JSON.stringify(fileData));

      const progressEl = document.getElementById('upload-progress');
      const progressBar = document.querySelector('#upload-progress .progress-bar');

      if (progressEl && progressBar) {
        progressEl.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = '0%';
        progressBar.setAttribute('aria-valuenow', '0');
      }

      this.isUploading = true;

      try {
        const response = await window.DOMUtils.csrfFetch(`/files/upload/${this.currentThreadId}/`, {
          method: 'POST',
          body: formData
        });

        if (response.ok) {
          this.handleUploadCompletion('file-input');
        } else {
          throw new Error('Upload failed');
        }
      } catch (error) {
        console.error('Upload error:', error);
        alert('Error uploading files');
        this.isUploading = false;
        if (progressEl) progressEl.style.display = 'none';
      }
    },

    async handleDirectoryUpload(files) {
      if (!files || files.length === 0) return;
      if (!this.currentThreadId) {
        alert('Please select a thread first');
        return;
      }

      const firstFile = files[0];
      const pathParts = firstFile.webkitRelativePath.split('/');
      const directoryName = pathParts[0];

      const fileCount = files.length;
      if (!confirm(`Upload directory "${directoryName}" with ${fileCount} files?`)) {
        this.handleUploadCompletion('directory-input', null, 0);
        return;
      }

      const formData = new FormData();
      const fileData = [];

      for (const file of files) {
        formData.append('files', file);
        const relativePath = `/${file.webkitRelativePath}`;
        formData.append('paths', relativePath);
        fileData.push({
          name: file.name,
          size: file.size,
          type: file.type,
          path: relativePath
        });
      }

      formData.append('file_data', JSON.stringify(fileData));

      const progressEl = document.getElementById('upload-progress');
      const progressBar = document.querySelector('#upload-progress .progress-bar');

      if (progressEl && progressBar) {
        progressEl.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = `Uploading ${directoryName}... 0%`;
        progressBar.setAttribute('aria-valuenow', '0');
      }

      this.isUploading = true;

      try {
        const response = await window.DOMUtils.csrfFetch(`/files/upload/${this.currentThreadId}/`, {
          method: 'POST',
          body: formData
        });

        if (response.ok) {
          this.handleUploadCompletion('directory-input', `${directoryName} uploaded successfully!`, 2000);
        } else {
          throw new Error('Directory upload failed');
        }
      } catch (error) {
        console.error('Directory upload error:', error);
        alert(`Error uploading directory "${directoryName}"`);
        this.isUploading = false;
        if (progressEl) progressEl.style.display = 'none';
        this.handleUploadCompletion('directory-input', null, 0);
      }
    },

    connectWebSocket() {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

      if (!this.currentThreadId) return;

      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${location.host}/ws/files/${this.currentThreadId}/`;

      this.ws = new WebSocket(wsUrl);

      this.ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          console.log('WS received:', data);

          if (data.type === 'file_update') {
            this.loadTree();
          } else if (data.type === 'progress') {
            console.log(`Progress update: ${data.progress}%`);
            const progressBar = document.querySelector('#upload-progress .progress-bar');
            if (progressBar && this.isUploading) {
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
    },

    // New method to handle thread changes
    async updateForThread(threadId) {
      this.currentThreadId = threadId;

      const filesColumn = document.getElementById('files-sidebar');
      if (!filesColumn || filesColumn.classList.contains('files-hidden')) {
        if (this.ws) {
          this.ws.close();
          this.ws = null;
        }
        return;
      }

      await this.loadSidebarContent();

      if (this.ws) {
        this.ws.close();
        this.ws = null;
      }

      await this.loadTree();

      // Re-apply saved tab for this thread
      const tid = this.currentThreadId || localStorage.getItem('lastThreadId') || null;
      const savedTab = tid ? (localStorage.getItem(`sidebarTab:${tid}`) || 'files') : 'files';
      this.applySidebarTabState(tid, savedTab);

      if (window.ResponsiveManager) {
        window.ResponsiveManager.syncFilesContent();
      }

      this.connectWebSocket();
    },

    // New method to handle thread deletion
    handleThreadDeletion() {
      this.currentThreadId = null;

      const filesColumn = document.getElementById('files-sidebar');
      if (!filesColumn || filesColumn.classList.contains('files-hidden')) {
        return;
      }

      if (!this.sidebarContentLoaded) {
        return;
      }

      if (this.ws) {
        this.ws.close();
        this.ws = null;
      }

      const treeContainer = document.getElementById('file-tree-container');
      if (treeContainer) {
        treeContainer.innerHTML = '<p class="text-muted p-3">No thread selected</p>';
      }
    },

    // Load webapps list into the sidebar
    loadWebappsList: async function () {
      const container = document.getElementById('webapps-list-container');
      if (!container) return;
      if (!this.currentThreadId) {
        container.innerHTML = '<p class="text-muted p-3">No thread selected.</p>';
        return;
      }
      try {
        const response = await fetch(`/apps/list/${this.currentThreadId}/`);
        if (!response.ok) throw new Error('Failed to load webapps list');
        const html = await response.text();
        container.innerHTML = html;
      } catch (err) {
        console.error('Error loading webapps list:', err);
        container.innerHTML = '<p class="text-danger p-3">Error loading webapps.</p>';
      }
    },

    // Announce split preview activation (layout handled by thread UI)
    activateSplitPreview: function (slug, url) {
      document.dispatchEvent(new CustomEvent('webapp_preview_activate', { detail: { slug, url } }));
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
    const initialTid = localStorage.getItem('lastThreadId') || null;
    if (initialTid && window.FileManager && typeof window.FileManager.updateForThread === 'function') {
      window.FileManager.updateForThread(initialTid);
    }
  });
})();


