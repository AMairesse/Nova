// static/nova/js/files.js
(function() {
  'use strict';

  window.FileManager = {
    currentThreadId: null,
    selectedFiles: new Set(),
    ws: null,
    isUploading: false,
    
    init() {
      // N'attacher que les événements pour les éléments présents au chargement
      this.attachInitialEventHandlers();
    },
    
    attachInitialEventHandlers() {
      // Seulement le bouton Files dans la navbar qui existe dès le chargement
      const filesBtn = document.getElementById('files-btn');
      if (filesBtn) {
        filesBtn.addEventListener('click', (e) => {
          e.preventDefault();
          this.toggleSidebar();
        });
      }
    },
    
    attachSidebarEventHandlers() {
      const uploadBtn = document.getElementById('upload-files-btn');
      if (uploadBtn) {
        uploadBtn.addEventListener('click', () => {
          const fileInput = document.getElementById('file-input');
          if (fileInput) fileInput.click();
        });
      }
      
      const fileInput = document.getElementById('file-input');
      if (fileInput) {
        fileInput.addEventListener('change', (e) => {
          this.handleFileUpload(e.target.files);
        });
      }
      
      const createFolderBtn = document.getElementById('create-folder-btn');
      if (createFolderBtn) {
        createFolderBtn.addEventListener('click', () => {
          this.createFolder();
        });
      }
    },
    
    async toggleSidebar() {
      console.log('Toggle sidebar called');
      
      const sidebar = document.getElementById('file-sidebar');
      const isOpen = sidebar.style.display === 'block';
      
      if (isOpen) {
        this.closeSidebar();
        return;
      }
      
      // Ouvrir la sidebar
      sidebar.style.display = 'block';
      document.body.classList.add('sidebar-open');
      
      this.currentThreadId = localStorage.getItem('lastThreadId');
      console.log('Current thread ID:', this.currentThreadId);
      
      const contentEl = document.getElementById('file-sidebar-content');
      if (!contentEl) {
        console.error('Sidebar content element not found');
        return;
      }
      
      // Vérifier si le contenu du panel est déjà chargé
      const treeContainer = document.getElementById('file-tree-container');
      if (!treeContainer) {
        console.log('Loading sidebar content...');
        try {
          const response = await fetch('/files/sidebar-panel/');
          if (!response.ok) throw new Error('Failed to load sidebar content');
          const html = await response.text();
          contentEl.innerHTML = html;
          
          this.attachSidebarEventHandlers();
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
      
      // Charger l'arbre et connecter WebSocket
      await this.loadTree();
      this.connectWebSocket();
    },

    closeSidebar() {
      const sidebar = document.getElementById('file-sidebar');
      sidebar.style.display = 'none';
      document.body.classList.remove('sidebar-open');
      
      // Fermer WebSocket
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
        const token = await window.getCSRFToken();
        const response = await fetch(`/files/list/${this.currentThreadId}/`, {
          headers: {
            'X-CSRFToken': token
          }
        });
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
        const isFolder = node.type === 'folder';
        const icon = isFolder ? 'bi-folder' : this.getFileIcon(node.mime_type);
        const nodeId = node.id || `temp-${Date.now()}-${Math.random()}`;
        
        html += `
          <li class="file-tree-item" data-id="${nodeId}" data-path="${node.path}" data-type="${node.type}">
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
            ${isFolder && node.children ? this.renderTree(node.children, node.path, level + 1) : ''}
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
        'application/x-zip': 'bi-file-zip'
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

    async deleteFile(fileId) {
      const item = document.querySelector(`[data-id="${fileId}"]`);
      if (!item) {
        console.error('File item not found');
        return;
      }
      
      const fileName = item.querySelector('.file-item-name').textContent;
      
      // Show confirmation dialog
      if (!confirm(`Are you sure you want to delete "${fileName}"?`)) {
        return;
      }
      
      try {
        const token = await window.getCSRFToken();
        const response = await fetch(`/files/delete/${fileId}/`, {
          method: 'DELETE',
          headers: {
            'X-CSRFToken': token,
            'Content-Type': 'application/json'
          }
        });
        
        if (response.ok) {
          // Remove from selected files if it was selected
          this.selectedFiles.delete(fileId);
          
          // Reload the file tree to reflect changes
          await this.loadTree();
          
          console.log(`File "${fileName}" deleted successfully`);
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
        const token = await window.getCSRFToken();
        const response = await fetch(`/files/upload/${this.currentThreadId}/`, {
          method: 'POST',
          body: formData,
          headers: {
            'X-CSRFToken': token
          }
        });
        
        if (response.ok) {
          // Clear the file input
          const fileInput = document.getElementById('file-input');
          if (fileInput) fileInput.value = '';
          
          // Wait a bit for final progress updates via WebSocket
          setTimeout(async () => {
            await this.loadTree();
            this.isUploading = false;
            if (progressEl) {
              // Keep progress bar visible for a moment to show completion
              setTimeout(() => {
                progressEl.style.display = 'none';
              }, 1000);
            }
          }, 500);
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

    async createFolder() {
      const name = prompt('Folder name:');
      if (!name) return;
      
      // Add the folder to the tree, under the current selection
      // TODO: implement this
    },

    connectWebSocket() {
      if (this.ws) this.ws.close();
      
      if (!this.currentThreadId) return;
      
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${protocol}//${location.host}/ws/files/${this.currentThreadId}/`;
      
      console.log('Connecting to WebSocket:', wsUrl);
      this.ws = new WebSocket(wsUrl);
      
      this.ws.onopen = () => {
        console.log('WebSocket connected for file operations');
      };
      
      this.ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          console.log('WebSocket message received:', data);
          
          if (data.type === 'file_update') {
            // Refresh tree on file updates
            this.loadTree();
          } else if (data.type === 'progress') {
            // Update progress bar
            const progressBar = document.querySelector('#upload-progress .progress-bar');
            if (progressBar && this.isUploading) {
              console.log(`Updating progress: ${data.progress}%`);
              progressBar.style.width = `${data.progress}%`;
              progressBar.textContent = `${data.progress}%`;
              progressBar.setAttribute('aria-valuenow', data.progress);
              
              // If progress reaches 100%, mark upload as complete
              if (data.progress >= 100) {
                console.log('Upload progress complete');
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
      
      this.ws.onclose = (event) => {
        console.log('WebSocket closed:', event.code, event.reason);
      };
    },

  };

  // Initialize when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    window.FileManager.init();
  });

})();
