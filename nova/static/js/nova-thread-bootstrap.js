// nova/static/js/nova-thread-bootstrap.js
// Single JS entrypoint executed by <script defer> (loaded last on thread pages).
(function () {
    'use strict';

    if (typeof window.ThreadManager?.init !== 'function') {
        console.error('[ThreadManager] init() is not available. Check script load order.');
        return;
    }

    // Guard is inside ThreadManager.init(); calling here is safe.
    window.ThreadManager.init();
})();
