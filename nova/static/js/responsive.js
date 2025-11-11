/* nova/static/js/responsive.js - Bootstrap-native responsive behavior */
(function () {
  'use strict';

  class ResponsiveManager {
    constructor() {
      this.isDesktop = window.innerWidth >= 992;
      this.filesVisible = true;
      this.init();
    }

    init() {
      this.setupEventListeners();
      this.syncMobileContent();
      this.setupFilesToggle();
      this.setupBootstrapEventListeners();
      this.setupMutationObserver();

      // Listen for thread changes to update FileManager
      document.addEventListener('threadChanged', (event) => {
        const threadId = event.detail?.threadId;
        if (threadId !== undefined && window.FileManager) {
          setTimeout(() => {
            window.FileManager.updateForThread(threadId);
          }, 100); // Small delay to ensure thread is loaded
        }
      });
    }

    setupEventListeners() {
      // Window resize handler
      window.addEventListener('resize', this.debounce(() => {
        const wasDesktop = this.isDesktop;
        this.isDesktop = window.innerWidth >= 992;

        if (wasDesktop !== this.isDesktop) {
          this.syncMobileContent();
          // Reset files visibility when switching between desktop/mobile
          if (this.isDesktop) {
            this.showFiles();
          }
        }
      }, 250));

      // Handle thread selection on mobile - close offcanvas ONLY when a thread link is clicked
      document.addEventListener('click', (e) => {
        const threadLink = e.target.closest('.thread-link');
        if (threadLink && !this.isDesktop) {
          const threadsOffcanvas = bootstrap.Offcanvas.getInstance(document.getElementById('threadsOffcanvas'));
          if (threadsOffcanvas) {
            threadsOffcanvas.hide();
          }
        }
      });

      // Sync mobile upload buttons with desktop ones
      this.syncUploadButtons();

      // Setup mobile Files/Webapps tab switching
      this.setupMobileFilesWebappsTabs();
    }

    setupFilesToggle() {
      const toggleBtn = document.getElementById('files-toggle-btn');
      if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
          if (this.isDesktop) {
            this.toggleFiles();
          }
        });
      }
    }

    toggleFiles() {
      if (this.filesVisible) {
        this.hideFiles();
      } else {
        this.showFiles();
      }
    }

    hideFiles() {
      const filesSidebar = document.getElementById('files-sidebar');
      const messageArea = document.getElementById('message-area');
      const toggleBtn = document.getElementById('files-toggle-btn');
      const toggleIcon = document.getElementById('files-toggle-icon');

      if (filesSidebar && messageArea) {
        filesSidebar.classList.add('files-hidden');
        messageArea.setAttribute('data-files-visible', 'false');

        if (toggleBtn) {
          toggleBtn.setAttribute('aria-expanded', 'false');
        }

        if (toggleIcon) {
          toggleIcon.className = 'bi bi-layout-sidebar-inset';
        }

        this.filesVisible = false;
      }
    }

    showFiles() {
      const filesSidebar = document.getElementById('files-sidebar');
      const messageArea = document.getElementById('message-area');
      const toggleBtn = document.getElementById('files-toggle-btn');
      const toggleIcon = document.getElementById('files-toggle-icon');

      if (filesSidebar && messageArea) {
        filesSidebar.classList.remove('files-hidden');
        messageArea.setAttribute('data-files-visible', 'true');

        if (toggleBtn) {
          toggleBtn.setAttribute('aria-expanded', 'true');
        }

        if (toggleIcon) {
          toggleIcon.className = 'bi bi-layout-sidebar-inset-reverse';
        }

        this.filesVisible = true;

        // When showing files panel, update FileManager for current thread
        if (window.FileManager && window.FileManager.currentThreadId) {
          setTimeout(() => {
            window.FileManager.updateForThread(window.FileManager.currentThreadId);
          }, 100);
        }
      }
    }

    syncMobileContent() {
      // Sync files content between desktop and mobile (desktop tree → mobile clone)
      // IMPORTANT: Do NOT call syncFilesContent() here, because syncFilesContent()
      // dispatches 'fileContentUpdated', and the global listener calls syncMobileContent().
      // That cycle caused the "too much recursion" error.
      this.syncUploadButtons();

      // Ensure FileManager is initialized once
      this.initializeFileManager();
    }

    initializeFileManager() {
      if (window.FileManager && !window.FileManager._initialized && typeof window.FileManager.init === 'function') {
        window.FileManager.init();
      }
    }

    syncFilesContent() {
      const desktopFilesContent = document.getElementById('file-sidebar-content');
      const mobileFilesContent = document.getElementById('file-sidebar-content-mobile');
      if (!desktopFilesContent || !mobileFilesContent) return;

      const tree = desktopFilesContent.querySelector('#file-tree-container');
      mobileFilesContent.innerHTML = '';
      if (tree) {
        const clone = tree.cloneNode(true);
        // Use a distinct id on mobile to avoid duplicate IDs
        clone.id = 'file-tree-container-mobile';
        mobileFilesContent.appendChild(clone);
      }

      // Let FileManager's delegated handlers work on cloned content
      const event = new Event('fileContentUpdated');
      document.dispatchEvent(event);
    }

    syncUploadButtons() {
      const desktopUploadBtn = document.getElementById('upload-files-btn');
      const mobileUploadBtn = document.getElementById('upload-files-btn-mobile');
      const desktopDirBtn = document.getElementById('upload-directory-btn');
      const mobileDirBtn = document.getElementById('upload-directory-btn-mobile');

      // For mobile we now trigger the shared hidden inputs directly from files.js
      // These sync handlers are kept for backward compatibility if desktop buttons exist.
      if (desktopUploadBtn && mobileUploadBtn && !mobileUploadBtn._synced) {
        mobileUploadBtn._synced = true;
        mobileUploadBtn.addEventListener('click', (e) => {
          e.preventDefault();
          desktopUploadBtn.click();
        });
      }

      if (desktopDirBtn && mobileDirBtn && !mobileDirBtn._synced) {
        mobileDirBtn._synced = true;
        mobileDirBtn.addEventListener('click', (e) => {
          e.preventDefault();
          desktopDirBtn.click();
        });
      }
    }

    setupMobileFilesWebappsTabs() {
      const tabFiles = document.getElementById('mobile-tab-files');
      const tabWebapps = document.getElementById('mobile-tab-webapps');
      const toolbar = document.getElementById('mobile-files-toolbar');
      const filesContainer = document.getElementById('file-sidebar-content-mobile');
      const webappsContainer = document.getElementById('webapps-list-container-mobile');

      if (!tabFiles || !tabWebapps || !filesContainer || !webappsContainer) return;

      const activateFiles = () => {
        tabFiles.classList.add('active');
        tabWebapps.classList.remove('active');
        if (toolbar) toolbar.classList.remove('d-none');
        filesContainer.classList.remove('d-none');
        filesContainer.removeAttribute('aria-hidden');
        webappsContainer.classList.add('d-none');
        webappsContainer.setAttribute('aria-hidden', 'true');
      };

      const activateWebapps = () => {
        tabWebapps.classList.add('active');
        tabFiles.classList.remove('active');
        if (toolbar) toolbar.classList.add('d-none');
        filesContainer.classList.add('d-none');
        filesContainer.setAttribute('aria-hidden', 'true');
        webappsContainer.classList.remove('d-none');
        webappsContainer.removeAttribute('aria-hidden');

        // Load mobile webapps list on demand
        if (window.WebappIntegration && typeof window.WebappIntegration.loadMobileWebappsList === 'function') {
          window.WebappIntegration.loadMobileWebappsList();
        }
      };

      tabFiles.addEventListener('click', (e) => {
        e.preventDefault();
        activateFiles();
      });

      tabWebapps.addEventListener('click', (e) => {
        e.preventDefault();
        activateWebapps();
      });

      // Default to Files view
      activateFiles();
    }

    syncThreadLists() {
      const desktopThreadList = document.querySelector('#threads-sidebar .list-group');
      const mobileThreadList = document.querySelector('#threadsOffcanvas .list-group');

      if (desktopThreadList && mobileThreadList) {
        mobileThreadList.innerHTML = desktopThreadList.innerHTML;
      }
    }

    setupBootstrapEventListeners() {
      // Sync content when offcanvas is shown
      const threadsOffcanvas = document.getElementById('threadsOffcanvas');
      const filesOffcanvas = document.getElementById('filesOffcanvas');

      if (threadsOffcanvas) {
        threadsOffcanvas.addEventListener('show.bs.offcanvas', () => {
          this.syncThreadLists();
        });
      }

      if (filesOffcanvas) {
        filesOffcanvas.addEventListener('show.bs.offcanvas', () => {
          this.syncFilesContent();
        });
      }

      // Auto-close threads offcanvas when thread is selected on mobile
      document.addEventListener('click', (e) => {
        if (e.target.closest('#threadsOffcanvas .thread-link') && !this.isDesktop) {
          const offcanvasInstance = bootstrap.Offcanvas.getInstance(threadsOffcanvas);
          if (offcanvasInstance) {
            offcanvasInstance.hide();
          }
        }
      });
    }

    setupMutationObserver() {
      // Watch for changes in desktop thread list and sync to mobile
      const desktopThreadList = document.querySelector('#threads-sidebar .list-group');
      if (desktopThreadList) {
        const observer = new MutationObserver(() => {
          this.syncThreadLists();
        });
        observer.observe(desktopThreadList, { childList: true, subtree: true });
      }
    }

    // Utility function for debouncing
    debounce(func, wait) {
      let timeout;
      return function executedFunction(...args) {
        const later = () => {
          clearTimeout(timeout);
          func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
      };
    }

    // Public method to get current state
    getCurrentState() {
      return {
        isDesktop: this.isDesktop
      };
    }

    // Public method to sync content manually
    syncContent() {
      this.syncMobileContent();
    }
  }

  // Initialize when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    window.ResponsiveManager = new ResponsiveManager();

    // Listen for file content updates:
    // This event is dispatched ONLY from syncFilesContent().
    // To avoid recursion:
    // - Do NOT call syncMobileContent() here (it used to call syncFilesContent()).
    // - Just ensure upload buttons and FileManager are wired for the new DOM.
    document.addEventListener('fileContentUpdated', () => {
      if (window.ResponsiveManager) {
        window.ResponsiveManager.syncUploadButtons();
        window.ResponsiveManager.initializeFileManager();
      }
    });

    // Listen for thread changes to update FileManager
    document.addEventListener('click', (e) => {
      const threadLink = e.target.closest('.thread-link');
      if (threadLink) {
        const threadId = threadLink.dataset.threadId;
        if (threadId && window.FileManager) {
          // Update FileManager for the new thread
          setTimeout(() => {
            window.FileManager.updateForThread(threadId);
          }, 100); // Small delay to ensure thread is loaded
        }
      }
    });
  });

  // Also expose the class for manual initialization if needed
  window.ResponsiveManagerClass = ResponsiveManager;
})();
