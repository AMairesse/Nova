// nova/static/js/thread-manager.js
/**
 * ThreadManager - Gestion unifiée des threads
 *
 * Consolidation de (fichiers historiques, désormais supprimés) :
 * - nova/static/js/thread_management.js (bootstrap / composition root)
 * - nova/static/js/thread-utils.js (DOM utils / merge)
 *
 * Et refactor de l'ancien thread-manager.js (pagination) dans ce fichier.
 *
 * Expose un unique namespace global : window.ThreadManager
 */
(function () {
    'use strict';

    // Keep the existing app namespace (non-module scripts)
    window.NovaApp = window.NovaApp || {};
    window.NovaApp.Modules = window.NovaApp.Modules || {};

    const getTranslator = () => (typeof window.gettext === 'function' ? window.gettext : (s) => s);

    const ThreadManager = {
        // ==========================================================================
        // === Configuration =========================================================
        // ==========================================================================
        config: {
            pagination: {
                limit: 10
            },
            groupOrder: ['today', 'yesterday', 'last_week', 'last_month', 'older'],
            selectors: {
                desktop: {
                    container: '#threads-list',
                    loadMoreButton: '#load-more-threads',
                    loadMoreContainer: '#load-more-container'
                },
                mobile: {
                    container: '#mobile-threads-list',
                    loadMoreButton: '#mobile-load-more-threads',
                    loadMoreContainer: '#mobile-load-more-container'
                }
            }
        },

        // ==========================================================================
        // === État =================================================================
        // ==========================================================================
        state: {
            debug: false,

            // Global guard: the thread UI must be bootstrapped exactly once per page.
            // Mirrors the previous behavior in thread_management.js.
            bootstrapped: false,

            // Preview-page-only bindings guard.
            previewPageBound: false,

            instances: {
                responsiveManager: null,
                messageManager: null
            },

            loading: {
                isLoading: false,
                initialized: false,
                handlersBound: false
            }
        },

        // ==========================================================================
        // === Utilitaires DOM (depuis thread-utils.js) ==============================
        // ==========================================================================
        UIUtils: {
            getGroupOrder() {
                return ThreadManager.config.groupOrder.slice();
            },

            getGroupTitle(key) {
                const t = getTranslator();
                switch (key) {
                    case 'today':
                        return t('Today');
                    case 'yesterday':
                        return t('Yesterday');
                    case 'last_week':
                        return t('Last Week');
                    case 'last_month':
                        return t('Last Month');
                    default:
                        return t('Older');
                }
            },

            /**
             * Ensure the thread group container exists and is inserted in the right order.
             *
             * Origin: thread-utils.js
             */
            ensureGroupContainer(group, containerEl) {
                const container = containerEl || document.getElementById('threads-list');
                if (!container) return null;

                let grp = container.querySelector(`.thread-group[data-group="${group}"]`);
                if (!grp) {
                    grp = document.createElement('div');
                    grp.className = 'thread-group mb-3';
                    grp.setAttribute('data-group', group);

                    const h6 = document.createElement('h6');
                    h6.className = 'text-muted mb-2 px-3 pt-2 pb-1 border-bottom';
                    h6.textContent = ThreadManager.UIUtils.getGroupTitle(group);

                    const ul = document.createElement('ul');
                    ul.className = 'list-group list-group-flush';

                    grp.appendChild(h6);
                    grp.appendChild(ul);

                    // Insert in correct order
                    const order = ThreadManager.UIUtils.getGroupOrder();
                    const targetIndex = order.indexOf(group);
                    let insertBefore = null;

                    const groups = Array.from(container.querySelectorAll('.thread-group'));
                    for (const g of groups) {
                        const idx = order.indexOf(g.dataset.group || 'older');
                        if (idx > targetIndex) {
                            insertBefore = g;
                            break;
                        }
                    }
                    container.insertBefore(grp, insertBefore);
                }

                return grp;
            },

            /**
             * Merge incoming thread groups HTML into the existing container, avoiding
             * duplicated group headers.
             *
             * Origin: thread-utils.js
             */
            mergeThreadGroupsFromHtml(html, containerEl) {
                const container = containerEl || document.getElementById('threads-list') || document.body;

                const tmp = document.createElement('div');
                tmp.innerHTML = html;

                const incomingGroups = tmp.querySelectorAll('.thread-group');
                incomingGroups.forEach((incoming) => {
                    const group = incoming.dataset.group || 'older';

                    // Find/create target group in the container
                    let targetGroup = container.querySelector(`.thread-group[data-group="${group}"]`);
                    if (!targetGroup) {
                        targetGroup = ThreadManager.UIUtils.ensureGroupContainer(group, container);
                    }
                    if (!targetGroup) return;

                    const incomingUl = incoming.querySelector('ul.list-group');
                    const targetUl = targetGroup.querySelector('ul.list-group');
                    if (!incomingUl || !targetUl) return;

                    // Append all new threads to the existing group
                    while (incomingUl.firstElementChild) {
                        targetUl.appendChild(incomingUl.firstElementChild);
                    }
                });
            }
        },

        // ==========================================================================
        // === Chargement & Pagination (depuis thread-manager.js) =====================
        // ==========================================================================
        Loading: {
            init() {
                if (ThreadManager.state.loading.initialized) return;
                ThreadManager.state.loading.initialized = true;
                ThreadManager.Loading.attachLoadMoreHandlers();
            },

            attachLoadMoreHandlers() {
                if (ThreadManager.state.loading.handlersBound) return;
                ThreadManager.state.loading.handlersBound = true;

                document.addEventListener('click', (e) => {
                    // Desktop load more button
                    if (e.target.matches('#load-more-threads') || e.target.closest('#load-more-threads')) {
                        e.preventDefault();
                        const btn = e.target.closest('#load-more-threads');
                        ThreadManager.Loading.loadMoreThreads(
                            btn,
                            ThreadManager.config.selectors.desktop.container,
                            ThreadManager.config.selectors.desktop.loadMoreContainer
                        );
                        return;
                    }

                    // Mobile load more button
                    if (e.target.matches('#mobile-load-more-threads') || e.target.closest('#mobile-load-more-threads')) {
                        e.preventDefault();
                        const btn = e.target.closest('#mobile-load-more-threads');
                        ThreadManager.Loading.loadMoreThreads(
                            btn,
                            ThreadManager.config.selectors.mobile.container,
                            ThreadManager.config.selectors.mobile.loadMoreContainer
                        );
                    }
                });
            },

            async loadMoreThreads(button, containerSelector, buttonContainerSelector) {
                if (!button) return;
                if (ThreadManager.state.loading.isLoading) return;

                ThreadManager.state.loading.isLoading = true;

                const offset = parseInt(button.dataset.offset, 10) || 0;
                const limit = ThreadManager.config.pagination.limit;

                // Show loading state
                button.disabled = true;
                const icon = button.querySelector('i');
                if (icon) icon.className = 'bi bi-hourglass-split me-1';

                try {
                    const baseUrl = window.NovaApp?.urls?.loadMoreThreads;
                    if (!baseUrl) {
                        throw new Error('NovaApp.urls.loadMoreThreads is not configured');
                    }

                    const response = await fetch(`${baseUrl}?offset=${offset}&limit=${limit}`);
                    const data = await response.json();

                    if (data.html) {
                        const container = document.querySelector(containerSelector);
                        if (container) {
                            ThreadManager.UIUtils.mergeThreadGroupsFromHtml(data.html, container);

                            if (data.has_more) {
                                button.dataset.offset = data.next_offset;
                                button.disabled = false;
                                const newIcon = button.querySelector('i');
                                if (newIcon) newIcon.className = 'bi bi-arrow-down-circle me-1';
                            } else {
                                const buttonContainer = document.querySelector(buttonContainerSelector);
                                if (buttonContainer) buttonContainer.remove();
                            }
                        }
                    }
                } catch (error) {
                    console.error('Error loading more threads:', error);

                    // Reset button state on error
                    button.disabled = false;
                    const resetIcon = button.querySelector('i');
                    if (resetIcon) resetIcon.className = 'bi bi-arrow-down-circle me-1';
                } finally {
                    ThreadManager.state.loading.isLoading = false;
                }
            }
        },

        // ==========================================================================
        // === Initialisation & Event Handlers (depuis thread_management.js) ========== 
        // ==========================================================================

        /**
         * Single entrypoint for the thread UI.
         *
         * IMPORTANT:
         * - No module should self-initialize.
         * - This bootstrap must be called exactly once per page.
         * - Guarded to prevent double bindings.
         *
         * Origin: thread_management.js
         */
        init(options) {
            const opts = options || {};
            const debug = Boolean(
                (typeof opts.debug === 'boolean' ? opts.debug : null) ??
                (typeof window.NovaApp.debug === 'boolean' ? window.NovaApp.debug : null) ??
                false
            );

            ThreadManager.state.debug = debug;
            const log = (...args) => {
                if (debug) console.debug('[ThreadManager]', ...args);
            };

            if (ThreadManager.state.bootstrapped) {
                if (debug) console.warn('[ThreadManager] init() called more than once; ignoring.');
                return ThreadManager;
            }
            ThreadManager.state.bootstrapped = true;

            // ---- Responsive (layout / offcanvas / sync) -----------------------------
            if (window.NovaApp.Modules.ResponsiveManager) {
                if (!ThreadManager.state.instances.responsiveManager) {
                    ThreadManager.state.instances.responsiveManager = new window.NovaApp.Modules.ResponsiveManager();
                }

                // Backward compatibility for legacy code expecting window.ResponsiveManager instance
                window.ResponsiveManager = ThreadManager.state.instances.responsiveManager;

                if (typeof ThreadManager.state.instances.responsiveManager.bind === 'function') {
                    ThreadManager.state.instances.responsiveManager.bind();
                }
            }

            // ---- Files facade (delegated handlers + per-thread sync) -----------------
            if (window.FileManager && typeof window.FileManager.init === 'function') {
                window.FileManager.init();
            }

            // ---- Preview manager (split preview) ------------------------------------
            if (window.PreviewManager && typeof window.PreviewManager.init === 'function') {
                window.PreviewManager.init();
            }

            // ---- Messages (thread selection, message send, streaming) ----------------
            if (typeof window.MessageManager === 'function') {
                if (!ThreadManager.state.instances.messageManager) {
                    ThreadManager.state.instances.messageManager = new window.MessageManager();
                }

                // Expose for existing inline hooks (modal buttons, etc.)
                window.NovaApp.messageManager = ThreadManager.state.instances.messageManager;

                if (typeof ThreadManager.state.instances.messageManager.init === 'function') {
                    ThreadManager.state.instances.messageManager.init();
                }
            }

            // ---- Thread loading (pagination) ----------------------------------------
            ThreadManager.Loading.init();

            // ---- Preview page (full-page webapp preview) ----------------------------
            ThreadManager._initPreviewPageUI({ debug });

            // Ensure initial sync once core managers are ready.
            const rm = ThreadManager.state.instances.responsiveManager;
            if (rm && typeof rm.syncContent === 'function') {
                rm.syncContent();
            }

            log('Thread UI bootstrapped');
            return ThreadManager;
        },

        // ==========================================================================
        // === Private helpers =======================================================
        // ==========================================================================

        /**
         * Preview-page-only bindings.
         *
         * Origin: thread_management.js (bootstrapPreviewPageUI)
         */
        _initPreviewPageUI({ debug }) {
            const previewCloseFloating = document.getElementById('preview-close-floating');
            const isPreviewPage = Boolean(previewCloseFloating || document.getElementById('preview-pane'));
            if (!isPreviewPage) return;

            if (ThreadManager.state.previewPageBound) return;
            ThreadManager.state.previewPageBound = true;

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
                    if (debug) console.warn('[ThreadManager] Failed to dispatch webapp_preview_activate', e);
                }
            }
        }
    };

    // Expose unique global namespace
    window.ThreadManager = ThreadManager;
})();
