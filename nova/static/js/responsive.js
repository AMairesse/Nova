/* nova/static/js/responsive.js - Bootstrap-native responsive behavior */
(function() {
  'use strict';

  class ResponsiveManager {
    constructor() {
      this.isDesktop = window.innerWidth >= 992;
      this.init();
    }

    init() {
      this.setupEventListeners();
      this.syncMobileContent();
    }

    setupEventListeners() {
      // Window resize handler
      window.addEventListener('resize', this.debounce(() => {
        const wasDesktop = this.isDesktop;
        this.isDesktop = window.innerWidth >= 992;
        
        if (wasDesktop !== this.isDesktop) {
          this.syncMobileContent();
        }
      }, 250));

      // Handle thread selection on mobile - close offcanvas and show message area
      document.addEventListener('click', (e) => {
        const threadItem = e.target.closest('.list-group-item');
        if (threadItem && !this.isDesktop) {
          // Close threads offcanvas when a thread is selected on mobile
          const threadsOffcanvas = bootstrap.Offcanvas.getInstance(document.getElementById('threadsOffcanvas'));
          if (threadsOffcanvas) {
            threadsOffcanvas.hide();
          }
        }
      });

      // Sync mobile upload buttons with desktop ones
      this.syncUploadButtons();
    }

    syncMobileContent() {
      // Sync files content between desktop and mobile
      this.syncFilesContent();
      
      // Sync upload button functionality
      this.syncUploadButtons();
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
  });

  // Also expose the class for manual initialization if needed
  window.ResponsiveManagerClass = ResponsiveManager;
})();
