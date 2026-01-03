// nova/static/js/nova-thread-bootstrap.js
// Single JS entrypoint executed by <script defer> (loaded last on thread pages).
(function () {
    'use strict';

    window.NovaApp = window.NovaApp || {};

    if (typeof window.NovaApp.bootstrapThreadUI !== 'function') {
        console.error('[NovaApp] bootstrapThreadUI() is not available. Check script load order.');
        return;
    }

    // Guard is inside bootstrapThreadUI; calling here is safe.
    window.NovaApp.bootstrapThreadUI();
})();
