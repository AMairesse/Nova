/* nova/static/js/responsive.js - Responsive behavior and mobile navigation */
(function() {
  'use strict';

  class ResponsiveManager {
    constructor() {
      this.isDesktop = window.innerWidth >= 992;
      this.currentMobileTab = 'threads';
      this.messageContainerLoaded = false;
      
      this.init();
    }

    init() {
      this.setupEventListeners();
      this.handleResize();
      this.setupMobileTabSwitching();
      this.syncMobileContent();
    }

    setupEventListeners() {
      // Window resize handler
      window.addEventListener('resize', this.debounce(() => {
        this.handleResize();
      }, 250));

      // Mobile tab switching
      const mobileNavTabs = document.querySelectorAll('#mobile-nav .nav-link');
      mobileNavTabs.forEach(tab => {
        tab.addEventListener('click', (e) => {
          const targetPanel = e.target.closest('.nav-link').getAttribute('data-bs-target');
          this.handleMobileTabSwitch(targetPanel);
        });
      });

      // Handle thread selection on mobile
      document.addEventListener('click', (e) => {
        if (e.target.closest('.list-group-item') && !this.isDesktop) {
          // Switch to messages tab when a thread is selected on mobile
          this.switchToMobileTab('messages');
        }
      });
    }

    handleResize() {
      const wasDesktop = this.isDesktop;
      this.isDesktop = window.innerWidth >= 992;

      if (wasDesktop !== this.isDesktop) {
        this.syncMobileContent();
        
        if (this.isDesktop) {
          // Switching to desktop - ensure message container is in the right place
          this.moveMessageContainerToDesktop();
        } else {
          // Switching to mobile - handle current state
          this.handleMobileLayout();
        }
      }
    }

    setupMobileTabSwitching() {
      // Initialize Bootstrap tabs for mobile
      const triggerTabList = [].slice.call(document.querySelectorAll('#mobile-nav .nav-link'));
      triggerTabList.forEach((triggerEl) => {
        const tabTrigger = new bootstrap.Tab(triggerEl);
        
        triggerEl.addEventListener('click', (event) => {
          event.preventDefault();
          tabTrigger.show();
        });
      });
    }

    handleMobileTabSwitch(targetPanel) {
      const panelId = targetPanel.replace('#', '');
      
      switch(panelId) {
        case 'threads-panel':
          this.currentMobileTab = 'threads';
          break;
        case 'messages-panel':
          this.currentMobileTab = 'messages';
          this.loadMessageContainerToMobile();
          break;
        case 'files-panel':
          this.currentMobileTab = 'files';
          this.syncFilesContent();
          break;
      }
    }

    switchToMobileTab(tabName) {
      if (this.isDesktop) return;
      
      const tabButton = document.querySelector(`#${tabName}-tab`);
      if (tabButton) {
        const tab = new bootstrap.Tab(tabButton);
        tab.show();
        this.handleMobileTabSwitch(`#${tabName}-panel`);
      }
    }

    syncMobileContent() {
      if (!this.isDesktop) {
        this.syncFilesContent();
        if (this.currentMobileTab === 'messages') {
          this.loadMessageContainerToMobile();
        }
      }
    }

    loadMessageContainerToMobile() {
      if (this.isDesktop) return;

      const desktopContainer = document.getElementById('message-container');
      const mobileContainer = document.getElementById('mobile-message-container');
      
      if (desktopContainer && mobileContainer && !this.messageContainerLoaded) {
        // Move the message container content to mobile
        mobileContainer.innerHTML = desktopContainer.innerHTML;
        this.messageContainerLoaded = true;
        
        // Update any event listeners that might be attached to the container
        this.updateMessageContainerEvents(mobileContainer);
      }
    }

    moveMessageContainerToDesktop() {
      const desktopContainer = document.getElementById('message-container');
      const mobileContainer = document.getElementById('mobile-message-container');
      
      if (desktopContainer && mobileContainer && this.messageContainerLoaded) {
        // Move content back to desktop
        desktopContainer.innerHTML = mobileContainer.innerHTML;
        mobileContainer.innerHTML = '';
        this.messageContainerLoaded = false;
        
        // Update any event listeners
        this.updateMessageContainerEvents(desktopContainer);
      }
    }

    updateMessageContainerEvents(container) {
      // Re-trigger any initialization that might be needed for the message container
      // This is a placeholder for any specific event handling that might be needed
      const event = new CustomEvent('messageContainerMoved', { 
        detail: { container: container, isMobile: !this.isDesktop }
      });
      document.dispatchEvent(event);
    }

    syncFilesContent() {
      if (this.isDesktop) return;

      const desktopFilesContent = document.getElementById('file-sidebar-content');
      const mobileFilesContent = document.getElementById('file-sidebar-content-mobile');
      
      if (desktopFilesContent && mobileFilesContent) {
        mobileFilesContent.innerHTML = desktopFilesContent.innerHTML;
      }

      // Sync upload button functionality
      this.syncUploadButtons();
    }

    syncUploadButtons() {
      const desktopUploadBtn = document.getElementById('upload-files-btn');
      const mobileUploadBtn = document.getElementById('upload-files-btn-mobile');
      const desktopDirBtn = document.getElementById('upload-directory-btn');
      const mobileDirBtn = document.getElementById('upload-directory-btn-mobile');

      if (desktopUploadBtn && mobileUploadBtn) {
        mobileUploadBtn.onclick = () => desktopUploadBtn.click();
      }
      
      if (desktopDirBtn && mobileDirBtn) {
        mobileDirBtn.onclick = () => desktopDirBtn.click();
      }
    }

    handleMobileLayout() {
      // Ensure proper mobile layout when switching from desktop
      if (this.currentMobileTab === 'messages' && !this.messageContainerLoaded) {
        this.loadMessageContainerToMobile();
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

    // Public method to switch tabs programmatically
    switchTab(tabName) {
      if (this.isDesktop) return;
      this.switchToMobileTab(tabName);
    }

    // Public method to get current state
    getCurrentState() {
      return {
        isDesktop: this.isDesktop,
        currentMobileTab: this.currentMobileTab,
        messageContainerLoaded: this.messageContainerLoaded
      };
    }
  }

  // Initialize when DOM is ready
  document.addEventListener('DOMContentLoaded', () => {
    window.ResponsiveManager = new ResponsiveManager();
  });

  // Also expose the class for manual initialization if needed
  window.ResponsiveManagerClass = ResponsiveManager;
})();
