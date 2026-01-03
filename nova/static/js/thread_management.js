// nova/static/js/thread_management.js
(function () {
  'use strict';

  // Global namespace (non-module scripts)
  window.NovaApp = window.NovaApp || {};
  window.NovaApp.Modules = window.NovaApp.Modules || {};

  /**
   * Single entrypoint for the thread UI.
   *
   * IMPORTANT:
   * - No module should self-initialize.
   * - This bootstrap must be called exactly once per page.
   * - Guarded to prevent double bindings.
   */
  function bootstrapThreadUI(options) {
    const opts = options || {};
    const debug = Boolean(
      (typeof opts.debug === 'boolean' ? opts.debug : null) ??
      (typeof window.NovaApp.debug === 'boolean' ? window.NovaApp.debug : null) ??
      false
    );

    if (window.__NOVA_THREAD_UI_BOOTSTRAPPED__) {
      if (debug) {
        console.warn('[NovaApp] bootstrapThreadUI() called more than once; ignoring.');
      }
      return window.NovaApp.threadUI || null;
    }
    window.__NOVA_THREAD_UI_BOOTSTRAPPED__ = true;

    const log = (...args) => {
      if (debug) console.debug('[NovaApp]', ...args);
    };

    // Composition root / instance registry
    const threadUI = (window.NovaApp.threadUI = window.NovaApp.threadUI || {
      instances: {},
      debug
    });

    // ---- Responsive (layout / offcanvas / sync) ---------------------------------
    if (window.NovaApp.Modules.ResponsiveManager) {
      if (!threadUI.instances.responsiveManager) {
        threadUI.instances.responsiveManager = new window.NovaApp.Modules.ResponsiveManager();
      }
      // Backward compatibility: some code expects window.ResponsiveManager instance
      window.ResponsiveManager = threadUI.instances.responsiveManager;
      if (typeof threadUI.instances.responsiveManager.bind === 'function') {
        threadUI.instances.responsiveManager.bind();
      }
    }

    // ---- Files facade (delegated handlers + per-thread sync) ---------------------
    if (window.FileManager && typeof window.FileManager.init === 'function') {
      window.FileManager.init();
    }

    // ---- Preview manager (split preview) ----------------------------------------
    if (window.PreviewManager && typeof window.PreviewManager.init === 'function') {
      window.PreviewManager.init();
    }

    // ---- Messages (thread selection, message send, streaming) -------------------
    if (typeof window.MessageManager === 'function') {
      if (!threadUI.instances.messageManager) {
        threadUI.instances.messageManager = new window.MessageManager();
      }
      // Expose for existing inline hooks (modal buttons, etc.)
      window.NovaApp.messageManager = threadUI.instances.messageManager;

      if (typeof threadUI.instances.messageManager.init === 'function') {
        threadUI.instances.messageManager.init();
      }
    }

    // ---- Thread loading (pagination) --------------------------------------------
    if (typeof window.ThreadLoadingManager === 'function') {
      if (!threadUI.instances.threadLoadingManager) {
        threadUI.instances.threadLoadingManager = new window.ThreadLoadingManager();
      }
      if (typeof threadUI.instances.threadLoadingManager.init === 'function') {
        threadUI.instances.threadLoadingManager.init();
      }
    }

    // ---- Preview page (full-page webapp preview) --------------------------------
    bootstrapPreviewPageUI({ debug });

    // Ensure initial sync once core managers are ready.
    if (
      threadUI.instances.responsiveManager &&
      typeof threadUI.instances.responsiveManager.syncContent === 'function'
    ) {
      threadUI.instances.responsiveManager.syncContent();
    }

    log('Thread UI bootstrapped');
    return threadUI;
  }

  function bootstrapPreviewPageUI({ debug }) {
    const previewCloseFloating = document.getElementById('preview-close-floating');
    const isPreviewPage = Boolean(previewCloseFloating || document.getElementById('preview-pane'));
    if (!isPreviewPage) return;

    // Idempotence for preview-page-only bindings
    if (window.__NOVA_PREVIEW_PAGE_BOUND__) return;
    window.__NOVA_PREVIEW_PAGE_BOUND__ = true;

    const navigateBack = () => {
      if (window.history.length > 1) {
        window.history.back();
      } else {
        window.location.href = window.NovaApp.urls?.index || '/';
      }
    };

    const closeBtn = document.getElementById('webapp-close-btn');
    if (closeBtn && !closeBtn._novaBoundNavBack) {
      closeBtn._novaBoundNavBack = true;
      closeBtn.addEventListener('click', navigateBack);
    }
    if (previewCloseFloating && !previewCloseFloating._novaBoundNavBack) {
      previewCloseFloating._novaBoundNavBack = true;
      previewCloseFloating.addEventListener('click', navigateBack);
    }

    // If template provided an explicit initial preview URL (public_url), announce it
    // after PreviewManager is initialized.
    const cfg = window.NovaApp && window.NovaApp.previewConfig;
    if (cfg && cfg.slug) {
      try {
        document.dispatchEvent(
          new CustomEvent('webapp_preview_activate', {
            detail: { slug: cfg.slug, url: cfg.url || `/apps/${cfg.slug}/` }
          })
        );
      } catch (e) {
        if (debug) console.warn('[NovaApp] Failed to dispatch webapp_preview_activate', e);
      }
    }
  }

  // Expose entrypoint (no auto init here)
  window.NovaApp.bootstrapThreadUI = bootstrapThreadUI;
})();
