/* nova/static/js/responsive.js - Bootstrap-native responsive behavior */
(function() {
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
      // Sync files content between desktop and mobile
      this.syncFilesContent();
      
      // Sync upload button functionality
      this.syncUploadButtons();
      
      // Initialize FileManager if not already done
      this.initializeFileManager();
    }

    initializeFileManager() {
      if (window.FileManager && !window.FileManager.initialized) {
        // Load sidebar content and initialize FileManager
        window.FileManager.loadSidebarContent().then(() => {
          // Get current thread ID from localStorage or thread management
          const currentThreadId = localStorage.getItem('lastThreadId');
          if (currentThreadId) {
            window.FileManager.updateForThread(currentThreadId);
          }
        });
        window.FileManager.initialized = true;
      }
    }

    syncFilesContent() {
      const desktopFilesContent = document.getElementById('file-sidebar-content');
      const mobileFilesContent = document.getElementById('file-sidebar-content-mobile');
      
      if (desktopFilesContent && mobileFilesContent) {
        // Clone content from desktop to mobile
        mobileFilesContent.innerHTML = desktopFilesContent.innerHTML;
      }
    }

    syncUploadButtons() {
      const desktopUploadBtn = document.getElementById('upload-files-btn');
      const mobileUploadBtn = document.getElementById('upload-files-btn-mobile');
      const desktopDirBtn = document.getElementById('upload-directory-btn');
      const mobileDirBtn = document.getElementById('upload-directory-btn-mobile');

      // Sync upload files button
      if (desktopUploadBtn && mobileUploadBtn) {
        mobileUploadBtn.onclick = (e) => {
          e.preventDefault();
          desktopUploadBtn.click();
        };
      }
      
      // Sync upload directory button
      if (desktopDirBtn && mobileDirBtn) {
        mobileDirBtn.onclick = (e) => {
          e.preventDefault();
          desktopDirBtn.click();
        };
      }
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
    
    // Listen for file content updates and sync to mobile
    document.addEventListener('fileContentUpdated', () => {
      if (window.ResponsiveManager) {
        window.ResponsiveManager.syncContent();
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
