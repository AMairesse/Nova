// static/nova/js/webapp-integration.js
(function () {
    'use strict';

    window.WebappIntegration = {
        // Load webapps list into the sidebar
        loadWebappsList: async function () {
            const container = document.getElementById('webapps-list-container');
            if (!container) return;
            if (!window.FileManager.currentThreadId) {
                container.innerHTML = '<p class="text-muted p-3">No thread selected.</p>';
                return;
            }
            try {
                const response = await fetch(`/apps/list/${window.FileManager.currentThreadId}/`);
                if (!response.ok) throw new Error('Failed to load webapps list');
                const html = await response.text();
                container.innerHTML = html;
            } catch (err) {
                console.error('Error loading webapps list:', err);
                container.innerHTML = '<p class="text-danger p-3">Error loading webapps.</p>';
            }
        },

        // Announce split preview activation (layout handled by thread UI)
        activateSplitPreview: function (slug, url) {
            document.dispatchEvent(new CustomEvent('webapp_preview_activate', { detail: { slug, url } }));
        }
    };

})();