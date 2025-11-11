// nova/static/js/thread_management.js
(function () {
  'use strict';

  // Global namespace
  window.NovaApp = window.NovaApp || {};

  /**
   * Central bootstrap for the thread UI.
   *
   * Responsibilities:
   * - Instantiate and initialize core managers once.
   * - Act as the single DOMContentLoaded entrypoint for thread pages.
   * - Call optional init hooks exposed by other modules (FileManager, ResponsiveManager, PreviewManager, etc.).
   *
   * This reduces scattered DOMContentLoaded handlers and implicit ordering assumptions
   * across multiple files.
   */
  function bootstrapThreadUI() {
    // Instantiate and init MessageManager
    if (typeof window.MessageManager === 'function') {
      if (!window.NovaApp.messageManager) {
        window.NovaApp.messageManager = new window.MessageManager();
      }
      if (typeof window.NovaApp.messageManager.init === 'function') {
        window.NovaApp.messageManager.init();
      }
    }

    // Instantiate and init ThreadLoadingManager
    if (typeof window.ThreadLoadingManager === 'function') {
      if (!window.NovaApp.threadLoadingManager) {
        window.NovaApp.threadLoadingManager = new window.ThreadLoadingManager();
      }
      if (typeof window.NovaApp.threadLoadingManager.init === 'function') {
        window.NovaApp.threadLoadingManager.init();
      }
    }

    // Optional: PreviewManager (defined only on pages that support split preview)
    if (typeof window.PreviewManager !== 'undefined' &&
      window.PreviewManager &&
      typeof window.PreviewManager.init === 'function') {
      window.PreviewManager.init();
    }

    // Optional: FileManager hook
    if (window.FileManager &&
      typeof window.FileManager.init === 'function' &&
      !window.FileManager._initialized) {
      window.FileManager.init();
      window.FileManager._initialized = true;
    }

    // Optional: ResponsiveManager already self-inits; no-op if absent.
    if (window.ResponsiveManager &&
      typeof window.ResponsiveManager.syncContent === 'function') {
      // Ensure initial sync once core managers are ready.
      window.ResponsiveManager.syncContent();
    }
  }

  // Run bootstrap once DOM is ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapThreadUI);
  } else {
    bootstrapThreadUI();
  }

  // Expose for debugging / external triggering
  window.NovaApp.bootstrapThreadUI = bootstrapThreadUI;
  window.MessageManager = window.MessageManager;
  window.StreamingManager = window.StreamingManager;
  window.MessageRenderer = window.MessageRenderer;
  window.ThreadLoadingManager = window.ThreadLoadingManager;

})();
