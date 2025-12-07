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
                        if (window.ThreadUIUtils && typeof window.ThreadUIUtils.mergeThreadGroupsFromHtml === 'function') {
                            window.ThreadUIUtils.mergeThreadGroupsFromHtml(data.html, container);
                        } else {
                            console.error('ThreadUIUtils.mergeThreadGroupsFromHtml not found');
                            // Fallback: just append the HTML
                            container.insertAdjacentHTML('beforeend', data.html);
                        }

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

})();