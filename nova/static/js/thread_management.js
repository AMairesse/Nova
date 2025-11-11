// nova/static/js/thread_management.js
(function () {
  'use strict';

  // Configuration object for URLs (will be populated from template)
  window.NovaApp = window.NovaApp || {};

  // ============================================================================
  // INITIALIZATION
  // ============================================================================
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
      const messageManager = new window.MessageManager();
      messageManager.init();
      const threadLoadingManager = new window.ThreadLoadingManager();
      threadLoadingManager.init();
      if (typeof window.PreviewManager !== 'undefined') {
        window.PreviewManager.init();
      }
    });
  } else {
    const messageManager = new window.MessageManager();
    messageManager.init();
    const threadLoadingManager = new window.ThreadLoadingManager();
    threadLoadingManager.init();
    if (typeof window.PreviewManager !== 'undefined') {
      window.PreviewManager.init();
    }
  }

  // Expose for debugging
  window.MessageManager = window.MessageManager;
  window.StreamingManager = window.StreamingManager;
  window.MessageRenderer = window.MessageRenderer;
  window.ThreadLoadingManager = window.ThreadLoadingManager;

})();
