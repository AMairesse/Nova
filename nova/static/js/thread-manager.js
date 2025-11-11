// static/nova/js/thread-manager.js
(function () {
    'use strict';

    // ============================================================================
    // THREAD LOADING MANAGER - Handles pagination and grouping
    // ============================================================================
    window.ThreadLoadingManager = class ThreadLoadingManager {
        constructor() {
            this.isLoading = false;
        }

        init() {
            this.attachLoadMoreHandlers();
        }

        attachLoadMoreHandlers() {
            // Desktop load more button
            document.addEventListener('click', (e) => {
                if (e.target.matches('#load-more-threads') || e.target.closest('#load-more-threads')) {
                    e.preventDefault();
                    const btn = e.target.closest('#load-more-threads');
                    this.loadMoreThreads(btn, '#threads-list', '#load-more-container');
                }
                // Mobile load more button
                else if (e.target.matches('#mobile-load-more-threads') || e.target.closest('#mobile-load-more-threads')) {
                    e.preventDefault();
                    const btn = e.target.closest('#mobile-load-more-threads');
                    this.loadMoreThreads(btn, '#mobile-threads-list', '#mobile-load-more-container');
                }
            });
        }

        async loadMoreThreads(button, containerSelector, buttonContainerSelector) {
            if (this.isLoading) return;

            this.isLoading = true;
            const offset = parseInt(button.dataset.offset) || 0;

            // Show loading state
            button.disabled = true;
            const icon = button.querySelector('i');
            if (icon) icon.className = 'bi bi-hourglass-split me-1';

            try {
                const response = await fetch(`${window.NovaApp.urls.loadMoreThreads}?offset=${offset}&limit=10`);
                const data = await response.json();

                if (data.html) {
                    const container = document.querySelector(containerSelector);
                    if (container) {
                        // Merge incoming groups into existing ones instead of duplicating headers
                        mergeThreadGroupsFromHtml(data.html, container);

                        if (data.has_more) {
                            button.dataset.offset = data.next_offset;
                            button.disabled = false;
                            const icon = button.querySelector('i');
                            if (icon) icon.className = 'bi bi-arrow-down-circle me-1';
                        } else {
                            const buttonContainer = document.querySelector(buttonContainerSelector);
                            if (buttonContainer) {
                                // No more threads, remove the button container
                                buttonContainer.remove();
                            }
                        }
                    }
                }
            } catch (error) {
                console.error('Error loading more threads:', error);
                // Reset button state on error
                button.disabled = false;
                const icon = button.querySelector('i');
                if (icon) icon.className = 'bi bi-arrow-down-circle me-1';
            } finally {
                this.isLoading = false;
            }
        }
    };

    // Thread UI helpers for grouping and DOM manipulation
    function getGroupOrder() {
        return ['today', 'yesterday', 'last_week', 'last_month', 'older'];
    }
    function getGroupTitle(key) {
        const t = (typeof window.gettext === 'function') ? window.gettext : (s) => s;
        switch (key) {
            case 'today': return t('Today');
            case 'yesterday': return t('Yesterday');
            case 'last_week': return t('Last Week');
            case 'last_month': return t('Last Month');
            default: return t('Older');
        }
    }
    function ensureGroupContainer(group, containerEl) {
        // Use the threads-list container instead of threads-container
        const container = containerEl || document.getElementById('threads-list');
        if (!container) return null;

        let grp = container.querySelector(`.thread-group[data-group="${group}"]`);
        if (!grp) {
            grp = document.createElement('div');
            grp.className = 'thread-group mb-3';
            grp.setAttribute('data-group', group);

            const h6 = document.createElement('h6');
            h6.className = 'text-muted mb-2 px-3 pt-2 pb-1 border-bottom';
            h6.textContent = getGroupTitle(group);

            const ul = document.createElement('ul');
            ul.className = 'list-group list-group-flush';

            grp.appendChild(h6);
            grp.appendChild(ul);

            // Insert in correct order
            const order = getGroupOrder();
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
    }
    function mergeThreadGroupsFromHtml(html, containerEl) {
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        const incomingGroups = tmp.querySelectorAll('.thread-group');
        incomingGroups.forEach(incoming => {
            const group = incoming.dataset.group || 'older';

            // First, try to find existing group in the container
            let targetGroup = containerEl.querySelector(`.thread-group[data-group="${group}"]`);

            // If group doesn't exist, create it using ensureGroupContainer
            if (!targetGroup) {
                targetGroup = ensureGroupContainer(group, containerEl);
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

})();