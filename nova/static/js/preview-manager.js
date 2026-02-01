// static/nova/js/preview-manager.js
(function () {
    'use strict';

    // ============================================================================
    // MAIN INITIALIZATION
    // ============================================================================
    // Preview split manager for webapp iframe
    window.PreviewManager = (function () {
        let debounceTimer = null;
        let initialized = false;

        function el(id) { return document.getElementById(id); }
        function getThreadId() { return window.FileManager?.currentThreadId || null; }
        function isMobile() { return window.UIUtils.isMobile(); }

        function applyDefaultWidth() {
            document.documentElement.style.setProperty('--chat-pane-width', `30%`);
        }

        function attachIframeLoadHandler() {
            const iframe = el('webapp-iframe');
            const spinner = el('webapp-spinner');
            if (!iframe) return;
            iframe.addEventListener('load', () => {
                window.UIUtils.setSpinnerVisible('webapp-spinner', false);
            });
        }

        function cacheBust(url) {
            try {
                const u = new URL(url, window.location.origin);
                u.searchParams.set('v', Date.now().toString());
                return u.toString();
            } catch (_) {
                return url + (url.includes('?') ? '&' : '?') + 'v=' + Date.now();
            }
        }

        function refreshIframeIfMatches(slug) {
            const iframe = el('webapp-iframe');
            if (!iframe) return;
            const currentSlug = iframe.dataset.webappSlug || '';
            const src = iframe.getAttribute('src') || '';
            if (!src) return;
            if (currentSlug === slug || src.includes(`/apps/${slug}/`)) {
                window.UIUtils.setSpinnerVisible('webapp-spinner', true);
                iframe.setAttribute('src', cacheBust(src));
            }
        }

        function setDesktopLayoutActive(active) {
            const resizer = el('split-resizer');
            if (resizer) resizer.classList.toggle('d-none', !active);
        }

        function setMobileOverlayModeActive(active) {
            const chatPane = el('chat-pane');
            if (!chatPane) return;
            if (active) {
                chatPane.classList.add('position-absolute', 'top-0', 'start-0', 'w-100', 'h-100', 'bg-white', 'shadow');
                chatPane.classList.add('d-none'); // start hidden over preview
            } else {
                chatPane.classList.remove('position-absolute', 'top-0', 'start-0', 'w-100', 'h-100', 'bg-white', 'shadow', 'd-none');
            }
        }

        function openPreview(slug, url) {
            const threadId = getThreadId();
            const previewPane = el('preview-pane');
            const iframe = el('webapp-iframe');
            const openBtn = el('webapp-open-btn');
            const slugLbl = el('webapp-slug-label');

            if (!previewPane || !iframe) return;

            // No persistence of last preview per thread.

            // Apply width and show panels
            applyDefaultWidth();
            document.body.classList.add('preview-active');
            previewPane.classList.remove('d-none');
            setDesktopLayoutActive(!isMobile());
            setMobileOverlayModeActive(isMobile());

            // Set URL and labels
            const targetUrl = url || `/apps/${slug}/`;
            iframe.dataset.webappSlug = slug || '';
            window.UIUtils.setSpinnerVisible('webapp-spinner', true);
            iframe.setAttribute('src', targetUrl);
            if (openBtn) openBtn.setAttribute('href', targetUrl);
            if (slugLbl) slugLbl.textContent = slug ? `(${slug})` : '';
        }

        function closePreview() {
            const previewPane = el('preview-pane');
            const resizer = el('split-resizer');
            const chatPane = el('chat-pane');
            if (previewPane) previewPane.classList.add('d-none');
            if (resizer) resizer.classList.add('d-none');
            // Leave chat pane visible and reset width to full
            if (chatPane) {
                document.documentElement.style.setProperty('--chat-pane-width', '100%');
            }
            // Exit full-page split mode
            document.body.classList.remove('preview-active');
        }

        function handleResizeDrag() {
            const resizer = el('split-resizer');
            const container = el('split-container');
            if (!resizer || !container) return;

            resizer.addEventListener('mousedown', (e) => {
                e.preventDefault();
                const onMove = (ev) => {
                    const rect = container.getBoundingClientRect();
                    const x = ev.clientX - rect.left;
                    let pct = Math.round((x / rect.width) * 100);
                    pct = Math.min(80, Math.max(20, pct));
                    document.documentElement.style.setProperty('--chat-pane-width', `${pct}%`);
                };
                const onUp = () => {
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                    // No persistence.
                };
                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
            });

            // Keyboard resize
            resizer.addEventListener('keydown', (e) => {
                if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
                e.preventDefault();
                const style = getComputedStyle(document.documentElement).getPropertyValue('--chat-pane-width').trim();
                let pct = parseInt(style.replace('%', '')) || 30;
                pct += (e.key === 'ArrowRight' ? 2 : -2);
                pct = Math.min(80, Math.max(20, pct));
                document.documentElement.style.setProperty('--chat-pane-width', `${pct}%`);
                // No persistence.
            });
        }

        function bindControls() {
            const closeBtn = el('webapp-close-btn');
            const refreshBtn = el('webapp-refresh-btn');
            const mobileToggle = el('mobile-chat-toggle');
            const iframe = el('webapp-iframe');

            if (closeBtn) closeBtn.addEventListener('click', closePreview);
            if (refreshBtn) refreshBtn.addEventListener('click', () => {
                if (!iframe) return;
                const src = iframe.getAttribute('src') || '';
                if (!src) return;
                window.UIUtils.setSpinnerVisible('webapp-spinner', true);
                iframe.setAttribute('src', cacheBust(src));
            });
            if (mobileToggle) mobileToggle.addEventListener('click', () => {
                const chatPane = el('chat-pane');
                if (!chatPane) return;
                chatPane.classList.toggle('d-none');
            });

            // ESC focuses chat input
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    const textarea = document.querySelector('#message-container textarea[name="new_message"]');
                    if (textarea) textarea.focus();
                }
            });

            // Update layout mode on resize
            window.addEventListener('resize', () => {
                if (el('preview-pane')?.classList.contains('d-none')) return;
                setDesktopLayoutActive(!isMobile());
                setMobileOverlayModeActive(isMobile());
            });
        }

        function handleThreadChanged(threadId) {
            // No persistence; just close the preview when switching threads.
            if (!threadId) {
                closePreview();
                return;
            }
            closePreview();
        }

        function init() {
            if (initialized) return;
            initialized = true;

            attachIframeLoadHandler();
            handleResizeDrag();
            bindControls();
            applyDefaultWidth();

            // Event: open split preview
            document.addEventListener('webapp_preview_activate', (e) => {
                const { slug, url } = (e.detail || {});
                if (!slug) return;
                openPreview(slug, url);
            });

            // Event: webapp update debounced
            document.addEventListener('webapp_update', (e) => {
                const slug = (e.detail && e.detail.slug) || '';
                if (!slug) return;
                clearTimeout(debounceTimer);
                debounceTimer = window.DOMUtils.debounce(() => {
                    refreshIframeIfMatches(slug);
                }, 400);
            });

            // Restore on initial load
            // Initial open is handled by explicit `previewConfig` only.
            const cfg = window.NovaApp && window.NovaApp.previewConfig;
            if (cfg && cfg.threadId && cfg.slug) {
                openPreview(cfg.slug, cfg.url || `/apps/${cfg.slug}/`);
            }

            // Listen to thread changes
            document.addEventListener('threadChanged', (e) => {
                handleThreadChanged(e.detail?.threadId || null);
            });
        }

        return { init, openPreview, closePreview };
    })();

})();
