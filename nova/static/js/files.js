// static/nova/js/files.js
(function() {
  'use strict';

  window.FileManager = {
    currentThreadId: null,
    selectedFiles: new Set(),
    ws: null,
    isUploading: false,
    sidebarContentLoaded: false,  // Cache flag to avoid reloading content

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
    
    async toggleSidebar() {
      const sidebarEl = document.getElementById('file-sidebar');
      if (!sidebarEl) {
        console.error('Sidebar element not found');
        return;
      }
      
      const offcanvas = new bootstrap.Offcanvas(sidebarEl);
      const isOpen = sidebarEl.classList.contains('show');
      
      if (isOpen) {
        offcanvas.hide();
        this.closeSidebar();  // Clean up WS
        return;
      }
      
      // Open the offcanvas
      offcanvas.show();
      
      this.currentThreadId = localStorage.getItem('lastThreadId');
      
      const contentEl = document.getElementById('file-sidebar-content');
      if (!contentEl) {
        console.error('Sidebar content element not found');
        return;
      }
      
      // Load panel content only if not already loaded (cache)
      if (!this.sidebarContentLoaded) {
        try {
          const response = await fetch('/files/sidebar-panel/');
          if (!response.ok) throw new Error('Failed to load sidebar content');
          const html = await response.text();
          contentEl.innerHTML = html;
          
          this.attachSidebarEventHandlers();
          this.sidebarContentLoaded = true;
        } catch (error) {
          console.error('Error loading sidebar:', error);
          contentEl.innerHTML = '<p class="alert alert-danger">Error loading files panel.</p>';
          return;
        }
      }
      
      if (!this.currentThreadId) {
        console.warn('No thread ID - cannot load files');
        const treeContainer = document.getElementById('file-tree-container');
        if (treeContainer) {
          treeContainer.innerHTML = '<p class="alert alert-warning">Please select a thread first.</p>';
        }
        return;
      }
      
      // Load tree and connect WebSocket
      await this.loadTree();
      this.connectWebSocket();
    },

    closeSidebar() {
      // Close WebSocket
      if (this.ws) {
        this.ws.close();
        this.ws = null;
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
        const response = await window.DOMUtils.csrfFetch(`/files/list/${this.currentThreadId}/`);  // Fixed: Use csrfFetch (handles token internally)
        const data = await response.json();
        
        if (data.files) {
          treeContainer.innerHTML = this.renderTree(data.files);
          this.bindTreeEvents();
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
        return level === 0 ? '<p class="text-muted p-3">No files yet</p>' : '';
      }
      
      let html = `<ul class="file-tree-list ${level === 0 ? 'root' : ''}" style="padding-left: ${level * 20}px;">`;
      
      nodes.forEach(node => {
        const isFolder = node.type === 'dir' || node.type === 'folder';
        const icon = isFolder ? 'bi-folder' : this.getFileIcon(node.mime);
        const nodeId = node.id || `temp-${Date.now()}-${Math.random()}`;
        const nodePath = node.full_path || node.path;
        
        html += `
          <li class="file-tree-item" data-id="${nodeId}" data-path="${nodePath}" data-type="${node.type}">
            <div class="file-item-content">
              <span class="file-item-icon">
                <i class="bi ${icon}"></i>
              </span>
              <span class="file-item-name">${node.name}</span>
              <span class="file-item-actions">
                <button class="btn btn-sm btn-ghost file-delete-btn" onclick="FileManager.deleteFile('${nodeId}')" title="Delete file">
                  <i class="bi bi-trash"></i>
                </button>
              </span>
            </div>
            ${isFolder && node.children ? this.renderTree(node.children, nodePath, level + 1) : ''}
          </li>
        `;
      });
      
      html += '</ul>';
      return html;
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

    bindTreeEvents() {
      // Bind click events for file items
      document.querySelectorAll('.file-tree-item').forEach(item => {
        item.addEventListener('click', (e) => {
          if (!e.target.closest('.file-item-actions')) {
            this.selectFile(item.dataset.id);
          }
        });
      });
    },

    selectFile(fileId) {
      const item = document.querySelector(`[data-id="${fileId}"]`);
      if (!item) return;
      
      // Toggle selection
      if (this.selectedFiles.has(fileId)) {
        this.selectedFiles.delete(fileId);
        item.classList.remove('selected');
      } else {
        this.selectedFiles.add(fileId);
        item.classList.add('selected');
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

    async deleteFile(fileId) {
      const item = document.querySelector(`[data-id="${fileId}"]`);
      if (!item) {
        console.error('File item not found');
        return;
      }
      
      const fileName = item.querySelector('.file-item-name').textContent;
      const itemType = item.dataset.type;
      const itemPath = item.dataset.path;
      
      // Check if this is a directory
      const isDirectory = itemType === 'dir' || itemType === 'folder';
      
      if (isDirectory) {
        // Handle directory deletion
        await this.deleteDirectory(fileName, itemPath);
      } else {
        // Handle single file deletion
        await this.deleteSingleFile(fileId, fileName);
      }
    },

    async deleteSingleFile(fileId, fileName) {
      // Show confirmation dialog
      if (!confirm(`Are you sure you want to delete "${fileName}"?`)) {
        return;
      }
      
      try {
        const response = await window.DOMUtils.csrfFetch(`/files/delete/${fileId}/`, { method: 'DELETE' });  // Fixed: Use csrfFetch
        if (response.ok) {
          // Remove from selected files if it was selected
          this.selectedFiles.delete(fileId);
          
          // Reload the file tree to reflect changes
          await this.loadTree();
          
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
      // First, get the current file tree data to find all files in this directory
      try {
        const response = await window.DOMUtils.csrfFetch(`/files/list/${this.currentThreadId}/`);  // Fixed: Use csrfFetch
        const data = await response.json();
        
        if (!data.files) {
          alert('Error loading file list for directory deletion');
          return;
        }
        
        // Collect all file IDs within this directory
        const fileIds = this.collectFilesInDirectory(data.files, directoryPath);
        
        if (fileIds.length === 0) {
          alert(`Directory "${directoryName}" appears to be empty or contains no files to delete.`);
          return;
        }
        
        // Show confirmation dialog with file count
        if (!confirm(`Are you sure you want to delete directory "${directoryName}" and all ${fileIds.length} files inside it?`)) {
          return;
        }
        
        // Delete all files in the directory
        let deletedCount = 0;
        let failedCount = 0;
        
        for (const fileId of fileIds) {
          try {
            const deleteResponse = await window.DOMUtils.csrfFetch(`/files/delete/${fileId}/`, { method: 'DELETE' });  // Fixed: Use csrfFetch
            if (deleteResponse.ok) {
              deletedCount++;
              // Remove from selected files if it was selected
              this.selectedFiles.delete(fileId);
            } else {
              failedCount++;
              console.error(`Failed to delete file with ID: ${fileId}`);
            }
          } catch (error) {
            failedCount++;
            console.error(`Error deleting file with ID ${fileId}:`, error);
          }
        }
        
        // Reload the file tree to reflect changes
        await this.loadTree();
        
        // Show result message
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
      // Clear the input
      const input = document.getElementById(inputId);
      if (input) input.value = '';
      
      // Wait for final updates and reload tree
      setTimeout(async () => {
        await this.loadTree();
        this.isUploading = false;
        const progressEl = document.getElementById('upload-progress');
        if (progressEl) {
          if (progressMessage) {
            const progressBar = document.querySelector('#upload-progress .progress-bar');
            if (progressBar) progressBar.textContent = progressMessage;
          }
          // Hide progress bar after delay
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
      
      // Process files
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
      
      // Initialize progress bar
      const progressEl = document.getElementById('upload-progress');
      const progressBar = document.querySelector('#upload-progress .progress-bar');
      
      if (progressEl && progressBar) {
        progressEl.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = '0%';
        progressBar.setAttribute('aria-valuenow', '0');
      }
      
      // Set upload state
      this.isUploading = true;
      
      try {
        const response = await window.DOMUtils.csrfFetch(`/files/upload/${this.currentThreadId}/`, {  // Fixed: Use csrfFetch
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
      
      // Extract directory name from the first file's path
      const firstFile = files[0];
      const pathParts = firstFile.webkitRelativePath.split('/');
      const directoryName = pathParts[0];
      
      // Show confirmation with directory info
      const fileCount = files.length;
      if (!confirm(`Upload directory "${directoryName}" with ${fileCount} files?`)) {
        this.handleUploadCompletion('directory-input', null, 0);  // Just clear input
        return;
      }
      
      const formData = new FormData();
      const fileData = [];
      
      // Process files with their relative paths
      for (const file of files) {
        formData.append('files', file);
        // Use the webkitRelativePath to preserve directory structure
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
      
      // Initialize progress bar
      const progressEl = document.getElementById('upload-progress');
      const progressBar = document.querySelector('#upload-progress .progress-bar');
      
      if (progressEl && progressBar) {
        progressEl.style.display = 'block';
        progressBar.style.width = '0%';
        progressBar.textContent = `Uploading ${directoryName}... 0%`;
        progressBar.setAttribute('aria-valuenow', '0');
      }
      
      // Set upload state
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
        this.handleUploadCompletion('directory-input', null, 0);  // Clear on error
      }
    },

    connectWebSocket() {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) return;  // Avoid multiple connections
      
      if (!this.currentThreadId) return;
      
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${location.host}/ws/files/${this.currentThreadId}/`;
      
      this.ws = new WebSocket(wsUrl);
      
      this.ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          console.log('WS received:', data);  // Debug: Trace incoming messages
          
          if (data.type === 'file_update') {
            // Refresh tree on file updates
            this.loadTree();
          } else if (data.type === 'progress') {
            console.log(`Progress update: ${data.progress}%`);  // Debug: Confirm reception
            // Update progress bar
            const progressBar = document.querySelector('#upload-progress .progress-bar');
            if (progressBar && this.isUploading) {
              progressBar.style.width = `${data.progress}%`;
              progressBar.textContent = `${data.progress}%`;
              progressBar.setAttribute('aria-valuenow', data.progress);
              if (data.progress === 100) {
                // Optional: Add success class or message
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
  };
})();
