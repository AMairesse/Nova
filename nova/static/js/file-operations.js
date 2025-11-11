// static/nova/js/file-operations.js
(function () {
    'use strict';

    window.FileOperations = {
        // Download a file by ID
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

        // Delete a single file
        async deleteSingleFile(fileId, fileName) {
            if (!confirm(`Are you sure you want to delete "${fileName}"?`)) {
                return;
            }

            try {
                const response = await window.DOMUtils.csrfFetch(`/files/delete/${fileId}/`, { method: 'DELETE' });
                if (response.ok) {
                    // Reload the file tree to reflect changes
                    await window.FileManager.loadTree();

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

        // Delete an entire directory
        async deleteDirectory(directoryName, directoryPath) {
            try {
                const response = await window.DOMUtils.csrfFetch(`/files/list/${window.FileManager.currentThreadId}/`);
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

                await window.FileManager.loadTree();

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

        // Factorized function for upload completion handling
        handleUploadCompletion(inputId, progressMessage = null, delay = 1000) {
            const input = document.getElementById(inputId);
            if (input) input.value = '';

            setTimeout(async () => {
                await window.FileManager.loadTree();

                if (window.ResponsiveManager) {
                    window.ResponsiveManager.syncFilesContent();
                }

                window.FileManager.isUploading = false;
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

        // Handle file upload
        async handleFileUpload(files) {
            if (!files || files.length === 0) return;
            if (!window.FileManager.currentThreadId) {
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

            window.FileManager.isUploading = true;

            try {
                const response = await window.DOMUtils.csrfFetch(`/files/upload/${window.FileManager.currentThreadId}/`, {
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
                window.FileManager.isUploading = false;
                if (progressEl) progressEl.style.display = 'none';
            }
        },

        // Handle directory upload
        async handleDirectoryUpload(files) {
            if (!files || files.length === 0) return;
            if (!window.FileManager.currentThreadId) {
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

            window.FileManager.isUploading = true;

            try {
                const response = await window.DOMUtils.csrfFetch(`/files/upload/${window.FileManager.currentThreadId}/`, {
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
                window.FileManager.isUploading = false;
                if (progressEl) progressEl.style.display = 'none';
                this.handleUploadCompletion('directory-input', null, 0);
            }
        },

        // Render file tree
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
                data-path="${window.DOMUtils.escapeAttr(nodePath)}"
                data-file-name="${window.DOMUtils.escapeAttr(nodeName)}">
              <i class="bi ${icon} me-1"></i>
              <span class="file-item-name">${window.DOMUtils.escapeHTML(nodeName)}</span>
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
                data-file-id="${window.DOMUtils.escapeAttr(nodeId)}"
                data-file-name="${window.DOMUtils.escapeAttr(nodeName)}"
                data-type="file"
                data-path="${window.DOMUtils.escapeAttr(nodePath)}">
              <i class="bi ${icon} me-1"></i>
              <a href="#" class="file-download-link" data-action="download">${window.DOMUtils.escapeHTML(nodeName)}</a>
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

        // Get file icon based on MIME type
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
        }
    };

})();