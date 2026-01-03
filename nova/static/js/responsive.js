/* nova/static/js/responsive.js - Bootstrap-native responsive behavior */
(function () {
  'use strict';

  // Keep global namespace (non-module scripts)
  window.NovaApp = window.NovaApp || {};
  window.NovaApp.Modules = window.NovaApp.Modules || {};

  class ResponsiveManager {
    constructor() {
      this.isDesktop = window.innerWidth >= 992;
      this.filesVisible = true;

      // Idempotence
      this._bound = false;
      this._resizeHandler = null;
      this._mutationObserver = null;
    }

    /**
     * Bind all event listeners (idempotent).
     * NOTE: No module should self-initialize; call from NovaApp.bootstrapThreadUI().
     */
    bind() {
      if (this._bound) return;
      this._bound = true;

      this.setupEventListeners();
      this.setupFilesToggle();
      this.setupBootstrapEventListeners();
      this.setupMutationObserver();

      // Listen for file content updates:
      // This event is dispatched ONLY from syncFilesContent().
      // To avoid recursion:
      // - Do NOT call syncMobileContent() here (it used to call syncFilesContent()).
      // - Just ensure upload buttons are wired for the new DOM.
      document.addEventListener('fileContentUpdated', () => {
        this.syncUploadButtons();
      });

      // Initial sync once everything is bound
      this.syncMobileContent();
    }

    setupEventListeners() {
      // Window resize handler
      this._resizeHandler = this.debounce(() => {
        const wasDesktop = this.isDesktop;
        this.isDesktop = window.innerWidth >= 992;

        if (wasDesktop !== this.isDesktop) {
          this.syncMobileContent();
          // Reset files visibility when switching between desktop/mobile
          if (this.isDesktop) {
            this.showFiles();
          }
        }
      }, 250);

      window.addEventListener('resize', this._resizeHandler);

      // Sync mobile upload buttons with desktop ones
      this.syncUploadButtons();

      // Setup mobile Files/Webapps tab behavior (Bootstrap-native)
      this.setupMobileFilesWebappsBootstrapTabs();
    }

    setupFilesToggle() {
      const toggleBtn = document.getElementById('files-toggle-btn');
      if (!toggleBtn) return;

      if (toggleBtn._novaBoundFilesToggle) return;
      toggleBtn._novaBoundFilesToggle = true;

      toggleBtn.addEventListener('click', () => {
        if (this.isDesktop) {
          this.toggleFiles();
        }
      });
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

    setupMobileFilesWebappsBootstrapTabs() {
      const tabs = document.getElementById('mobile-files-webapps-tabs');
      const toolbar = document.getElementById('mobile-files-toolbar');
      const webappsContainer = document.getElementById('webapps-list-container-mobile');

      if (!tabs || !toolbar) return;
      if (tabs._novaBoundBsTabs) return;
      tabs._novaBoundBsTabs = true;

      const updateUIForActiveTab = (tabEl) => {
        if (!tabEl) return;

        const targetSelector = tabEl.getAttribute('data-bs-target') || tabEl.getAttribute('href') || '';
        const isWebapps = targetSelector === '#mobile-webapps-pane' || tabEl.id === 'mobile-tab-webapps';

        // Toggle toolbar visibility (toolbar only makes sense for Files)
        if (isWebapps) {
          toolbar.classList.add('d-none');
        } else {
          toolbar.classList.remove('d-none');
        }

        // Lazy-load webapps list when switching to Webapps tab
        if (isWebapps && webappsContainer) {
          if (window.WebappIntegration && typeof window.WebappIntegration.loadMobileWebappsList === 'function') {
            window.WebappIntegration.loadMobileWebappsList();
          }
        }
      };

      // Initial state: enforce toolbar visibility based on currently active tab
      const initialActiveTab = tabs.querySelector('.nav-link.active');
      updateUIForActiveTab(initialActiveTab);

      // Bootstrap-native event fired after a tab is shown
      tabs.addEventListener('shown.bs.tab', (event) => {
        updateUIForActiveTab(event.target);
      });
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

      if (threadsOffcanvas && !threadsOffcanvas._novaBoundShowSync) {
        threadsOffcanvas._novaBoundShowSync = true;
        threadsOffcanvas.addEventListener('show.bs.offcanvas', () => {
          this.syncThreadLists();
        });
      }

      if (filesOffcanvas && !filesOffcanvas._novaBoundShowSync) {
        filesOffcanvas._novaBoundShowSync = true;
        filesOffcanvas.addEventListener('show.bs.offcanvas', () => {
          this.syncFilesContent();
        });
      }
    }

    setupMutationObserver() {
      // Watch for changes in desktop thread list and sync to mobile
      const desktopThreadList = document.querySelector('#threads-sidebar .list-group');
      if (!desktopThreadList || this._mutationObserver) return;

      this._mutationObserver = new MutationObserver(() => {
        this.syncThreadLists();
      });
      this._mutationObserver.observe(desktopThreadList, { childList: true, subtree: true });
    }

    // Utility function for debouncing
    debounce(func, wait) {
      let timeout;
      return (...args) => {
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

  // Expose via NovaApp namespace (preferred)
  window.NovaApp.Modules.ResponsiveManager = ResponsiveManager;

  // Backward compatibility: some code may import the class from here
  window.ResponsiveManagerClass = ResponsiveManager;
})();
