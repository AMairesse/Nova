(function () {
    'use strict';

    const root = document.getElementById('continuous-page-root');
    if (!root) {
        return;
    }

    const DAY_PAGE_LIMIT = 30;
    const DAY_SEARCH_DEBOUNCE_MS = 200;
    const daySummaryUrlTemplate = root.dataset.urlDaySummary || '';
    const serverTodayLabel = (root.dataset.todayLabel || '').trim();
    const hasServerTodayLabel = /^\d{4}-\d{2}-\d{2}$/.test(serverTodayLabel);

    window.NovaApp = window.NovaApp || {};
    window.NovaApp.isContinuousPage = true;
    window.NovaApp.urls = {
        ...(window.NovaApp.urls || {}),
        index: root.dataset.urlIndex,
        addMessage: root.dataset.urlAddMessage,
        messageList: root.dataset.urlMessageList,
        runningTasksBase: root.dataset.urlRunningTasksBase,
        loadDays: root.dataset.urlLoadDays,
        regenerateSummary: root.dataset.urlRegenerateSummary,
        fileList: root.dataset.urlFileList,
        fileUpload: root.dataset.urlFileUpload,
        fileDelete: root.dataset.urlFileDelete,
        interactionAnswer: root.dataset.urlInteractionAnswer,
        interactionCancel: root.dataset.urlInteractionCancel,
    };

    const t = (window.gettext && typeof window.gettext === 'function') ? window.gettext : (s) => s;

    const state = {
        selectedDay: null,
        isDayPinnedInUrl: false,
        // Must come from server/user timezone rather than browser local clock.
        todayLabel: hasServerTodayLabel ? serverTodayLabel : getLocalISODate(),
        summarySocket: null,
        reconnectChecked: false,
        daysRefreshTimer: null,
        regenerateBusy: false,
        daysVisible: true,
        daysOffset: 0,
        daysHasMore: false,
        daysQuery: '',
        daysLoadingOlder: false,
        daysLimit: DAY_PAGE_LIMIT,
        daysSearchTimer: null,
        daysRequestSeq: 0,
    };

    function getLocalISODate() {
        const d = new Date();
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        return `${yyyy}-${mm}-${dd}`;
    }

    function isTodayDayLabel(day) {
        return !!day && day === state.todayLabel;
    }

    function isValidDayLabel(day) {
        return /^\d{4}-\d{2}-\d{2}$/.test(day || '');
    }

    function getEffectiveDay() {
        return state.selectedDay;
    }

    function getSelectedDayFromUrl() {
        const qs = new URLSearchParams(window.location.search);
        const day = qs.get('day');
        return isValidDayLabel(day) ? day : null;
    }

    function getSearchInputs() {
        return [
            document.getElementById('continuous-day-search-input'),
            document.getElementById('continuous-day-search-input-mobile'),
        ].filter(Boolean);
    }

    function getJumpInputs() {
        return [
            document.getElementById('continuous-day-jump-input'),
            document.getElementById('continuous-day-jump-input-mobile'),
        ].filter(Boolean);
    }

    function getLatestButtons() {
        return [
            document.getElementById('continuous-day-latest-btn'),
            document.getElementById('continuous-day-latest-btn-mobile'),
        ].filter(Boolean);
    }

    function getTodayButtons() {
        return [
            document.getElementById('continuous-day-today-btn'),
            document.getElementById('continuous-day-today-btn-mobile'),
        ].filter(Boolean);
    }

    function getDaysToggleButton() {
        return document.getElementById('continuous-days-toggle-btn');
    }

    function getDaysToggleIcon() {
        return document.getElementById('continuous-days-toggle-icon');
    }

    function getDaysScrollerForListId(listId) {
        if (listId === 'threads-list') {
            return document.getElementById('threads-container');
        }
        if (listId === 'mobile-threads-list') {
            return document.getElementById('mobile-threads-container');
        }
        return null;
    }

    function setJumpInputValues(day) {
        const value = day || '';
        getJumpInputs().forEach((input) => {
            if (input.value !== value) {
                input.value = value;
            }
        });
    }

    function setSearchInputValues(query, skipInput = null) {
        const value = query || '';
        getSearchInputs().forEach((input) => {
            if (skipInput && input === skipInput) {
                return;
            }
            if (input.value !== value) {
                input.value = value;
            }
        });
    }

    function updateQuickActionButtons() {
        const isLatest = !state.selectedDay;
        const isToday = state.selectedDay === state.todayLabel;

        getLatestButtons().forEach((btn) => {
            btn.classList.toggle('active', isLatest);
            btn.setAttribute('aria-pressed', isLatest ? 'true' : 'false');
        });
        getTodayButtons().forEach((btn) => {
            btn.classList.toggle('active', isToday);
            btn.setAttribute('aria-pressed', isToday ? 'true' : 'false');
        });
    }

    function getDaysSidebarVisibilityKey() {
        if (window.StorageUtils && typeof window.StorageUtils.getContinuousDaysSidebarVisibleKey === 'function') {
            return window.StorageUtils.getContinuousDaysSidebarVisibleKey();
        }
        return 'nova:continuousDaysSidebarVisible';
    }

    function readSavedDaysVisibility() {
        const key = getDaysSidebarVisibilityKey();
        if (window.StorageUtils && typeof window.StorageUtils.getItem === 'function') {
            return window.StorageUtils.getItem(key, 'true') !== 'false';
        }
        try {
            return localStorage.getItem(key) !== 'false';
        } catch (err) {
            return true;
        }
    }

    function saveDaysVisibility(visible) {
        const key = getDaysSidebarVisibilityKey();
        document.documentElement.classList.toggle('pref-continuous-days-hidden', !visible);
        if (window.StorageUtils && typeof window.StorageUtils.setItem === 'function') {
            window.StorageUtils.setItem(key, visible ? 'true' : 'false');
            return;
        }
        try {
            localStorage.setItem(key, visible ? 'true' : 'false');
        } catch (err) {
            // no-op
        }
    }

    function applyDaysVisibility(visible, { persist = false } = {}) {
        const normalized = Boolean(visible);
        const daysSidebar = document.getElementById('threads-sidebar');
        const messageArea = document.getElementById('message-area');
        const toggleBtn = getDaysToggleButton();
        const toggleIcon = getDaysToggleIcon();

        if (daysSidebar) {
            daysSidebar.classList.toggle('days-hidden', !normalized);
        }
        if (messageArea) {
            messageArea.setAttribute('data-days-visible', normalized ? 'true' : 'false');
        }
        if (toggleBtn) {
            toggleBtn.setAttribute('aria-expanded', normalized ? 'true' : 'false');
        }
        if (toggleIcon) {
            toggleIcon.className = normalized ? 'bi bi-layout-sidebar-inset' : 'bi bi-layout-sidebar-inset-reverse';
        }

        state.daysVisible = normalized;
        if (persist) {
            saveDaysVisibility(normalized);
        }
    }

    function showDaysSidebar() {
        applyDaysVisibility(true, { persist: true });
    }

    function hideDaysSidebar() {
        applyDaysVisibility(false, { persist: true });
    }

    function toggleDaysSidebar() {
        if (window.innerWidth < 992) {
            return;
        }
        if (state.daysVisible) {
            hideDaysSidebar();
        } else {
            showDaysSidebar();
        }
    }

    function buildDaySummaryUrl(day) {
        if (!daySummaryUrlTemplate || !day) {
            return '';
        }
        return daySummaryUrlTemplate.replace('__DAY__', encodeURIComponent(day));
    }

    function setSelectedDay(day) {
        const normalizedDay = isValidDayLabel(day) ? day : null;

        state.selectedDay = normalizedDay;
        state.isDayPinnedInUrl = !!state.selectedDay;

        const qs = new URLSearchParams(window.location.search);
        if (state.selectedDay) {
            qs.set('day', state.selectedDay);
        } else {
            qs.delete('day');
        }
        const nextUrl = `${window.location.pathname}${qs.toString() ? `?${qs.toString()}` : ''}`;
        window.history.replaceState({}, '', nextUrl);

        setJumpInputValues(state.selectedDay);
        updateQuickActionButtons();
        applyActiveDayInAllLists();
    }

    function setDaysQuery(query, skipInput = null) {
        state.daysQuery = (query || '').trim();
        setSearchInputValues(state.daysQuery, skipInput);
    }

    function applyPostingVisibility(day) {
        const allowPosting = !day || isTodayDayLabel(day);
        document.querySelectorAll('#message-container .message-input-area').forEach((el) => {
            el.style.display = allowPosting ? '' : 'none';
        });
    }

    function setRegenerateBusy(busy) {
        state.regenerateBusy = busy;
        const btn = document.getElementById('continuous-regenerate-summary');
        if (!btn) return;
        btn.disabled = busy;
        const icon = btn.querySelector('i');
        if (icon) {
            icon.className = busy ? 'bi bi-hourglass-split' : 'bi bi-arrow-clockwise';
        }
    }

    function setSummaryEvent(message, tone = 'muted') {
        const eventEl = document.getElementById('continuous-summary-updated-event');
        if (!eventEl) return;
        eventEl.style.display = message ? '' : 'none';
        eventEl.classList.remove('text-muted', 'text-danger', 'text-success');
        if (tone === 'danger') {
            eventEl.classList.add('text-danger');
        } else if (tone === 'success') {
            eventEl.classList.add('text-success');
        } else {
            eventEl.classList.add('text-muted');
        }
        eventEl.textContent = message || '';
    }

    function applyActiveDayInList(containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const activeDay = getEffectiveDay();

        container.querySelectorAll('[data-day-link="true"]').forEach((link) => {
            const isDefaultViewLink = link.dataset.defaultView === 'true';
            const isActive = activeDay ? link.dataset.dayLabel === activeDay : isDefaultViewLink;
            link.classList.toggle('active', isActive);
            link.classList.toggle('fw-semibold', isActive);
            link.setAttribute('aria-current', isActive ? 'true' : 'false');
        });
    }

    function applyActiveDayInAllLists() {
        applyActiveDayInList('threads-list');
        applyActiveDayInList('mobile-threads-list');
    }

    function getDaysLoadingHtml() {
        return `<div class="text-muted small p-3">${t('Loading days…')}</div>`;
    }

    function getDaysLoadErrorHtml() {
        return `<div class="text-danger small p-3">${t('Failed to load days.')}</div>`;
    }

    function buildDaysRequestUrl(offset, limit) {
        const params = new URLSearchParams();
        params.set('offset', String(offset));
        params.set('limit', String(limit));
        if (state.daysQuery) {
            params.set('q', state.daysQuery);
        }
        return `${window.NovaApp.urls.loadDays}?${params.toString()}`;
    }

    async function fetchDaysPage(offset, limit) {
        const reqId = ++state.daysRequestSeq;
        const url = buildDaysRequestUrl(offset, limit);
        const resp = await fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        if (!resp.ok) {
            throw new Error(`days_load_failed_${resp.status}`);
        }
        const data = await resp.json();
        if (reqId !== state.daysRequestSeq) {
            return null;
        }
        return data;
    }

    function createHtmlContainer(html) {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html || '';
        return wrapper;
    }

    function mergeDaysList(targetEl, html) {
        const incoming = createHtmlContainer(html);
        const targetList = targetEl.querySelector('ul.continuous-days-list-group');
        const incomingList = incoming.querySelector('ul.continuous-days-list-group');

        if (!targetList || !incomingList) {
            targetEl.innerHTML = html || `<div class="text-muted small p-3">${t('No days yet.')}</div>`;
            return;
        }

        targetList.querySelectorAll('[data-load-more-item="true"]').forEach((row) => row.remove());

        Array.from(incomingList.children).forEach((row) => {
            if (row.dataset.loadMoreItem === 'true') {
                targetList.appendChild(row.cloneNode(true));
                return;
            }

            const dayLink = row.querySelector('[data-day-link="true"]');
            if (dayLink) {
                const label = dayLink.dataset.dayLabel || '';
                const selector = `[data-day-link="true"][data-day-label="${label}"]`;
                if (!targetList.querySelector(selector)) {
                    targetList.appendChild(row.cloneNode(true));
                }
                return;
            }

            const monthKey = row.dataset.monthKeyHeader;
            if (monthKey) {
                const selector = `[data-month-key-header="${monthKey}"]`;
                if (!targetList.querySelector(selector)) {
                    targetList.appendChild(row.cloneNode(true));
                }
                return;
            }

            targetList.appendChild(row.cloneNode(true));
        });
    }

    function renderDaysHtml(containerId, html, { append = false, preserveScroll = false } = {}) {
        const el = document.getElementById(containerId);
        if (!el) return;

        const scroller = getDaysScrollerForListId(containerId);
        const previousScrollTop = preserveScroll && scroller ? scroller.scrollTop : null;

        if (append) {
            mergeDaysList(el, html);
        } else {
            el.innerHTML = html || `<div class="text-muted small p-3">${t('No days yet.')}</div>`;
        }

        if (previousScrollTop !== null && scroller) {
            scroller.scrollTop = previousScrollTop;
        }
        applyActiveDayInList(containerId);
    }

    function updateDaysState(data) {
        state.daysHasMore = Boolean(data && data.has_more);
        state.daysOffset = data && typeof data.next_offset === 'number' ? data.next_offset : 0;
        if (typeof data?.applied_query === 'string') {
            setDaysQuery(data.applied_query);
        }
        setLoadOlderBusy(false);
    }

    async function loadDays({ offset = 0, limit = state.daysLimit, append = false, withLoading = true, preserveScroll = false } = {}) {
        const desktopEl = document.getElementById('threads-list');
        const mobileEl = document.getElementById('mobile-threads-list');

        if (!append && withLoading) {
            if (desktopEl) desktopEl.innerHTML = getDaysLoadingHtml();
            if (mobileEl) mobileEl.innerHTML = getDaysLoadingHtml();
        }

        try {
            const data = await fetchDaysPage(offset, limit);
            if (!data) {
                return;
            }

            renderDaysHtml('threads-list', data.html, { append, preserveScroll });
            renderDaysHtml('mobile-threads-list', data.html, { append, preserveScroll });
            updateDaysState(data);
        } catch (err) {
            if (!append) {
                if (desktopEl) desktopEl.innerHTML = getDaysLoadErrorHtml();
                if (mobileEl) mobileEl.innerHTML = getDaysLoadErrorHtml();
            }
            setLoadOlderBusy(false);
        }
    }

    function scheduleDaysReload() {
        if (state.daysRefreshTimer) {
            window.clearTimeout(state.daysRefreshTimer);
        }
        state.daysRefreshTimer = window.setTimeout(() => {
            loadDays({ offset: 0, append: false, withLoading: false, preserveScroll: true });
        }, 150);
    }

    function scheduleDaysSearchReload() {
        if (state.daysSearchTimer) {
            window.clearTimeout(state.daysSearchTimer);
        }
        state.daysSearchTimer = window.setTimeout(() => {
            loadDays({ offset: 0, append: false, withLoading: true, preserveScroll: false });
        }, DAY_SEARCH_DEBOUNCE_MS);
    }

    function setLoadOlderBusy(busy) {
        state.daysLoadingOlder = busy;
        document.querySelectorAll('[data-action="continuous-load-older"]').forEach((btn) => {
            btn.disabled = busy;
            const icon = btn.querySelector('i');
            if (icon) {
                icon.className = busy ? 'bi bi-hourglass-split me-1' : 'bi bi-arrow-down-circle me-1';
            }
        });
    }

    function ensureSummaryInsideScroll() {
        const panel = document.getElementById('continuous-day-summary');
        const scroll = document.querySelector('#message-container #conversation-container');
        if (!panel || !scroll) return;
        if (panel.parentElement !== scroll) {
            scroll.prepend(panel);
        }
    }

    async function loadMessages(day) {
        const summaryPanel = document.getElementById('continuous-day-summary');
        if (summaryPanel && summaryPanel.parentElement) {
            summaryPanel.remove();
        }

        const params = day ? `?day=${encodeURIComponent(day)}` : '';
        const resp = await fetch(`${window.NovaApp.urls.messageList}${params}`, { headers: { 'X-AJAX': 'true' } });
        if (!resp.ok) {
            throw new Error(`messages_load_failed_${resp.status}`);
        }
        const html = await resp.text();
        const mc = document.getElementById('message-container');
        if (mc) mc.innerHTML = html;

        if (
            window.NovaApp.messageManager
            && typeof window.NovaApp.messageManager.applyTemplateSetupPrefillFromUrl === 'function'
        ) {
            window.NovaApp.messageManager.applyTemplateSetupPrefillFromUrl();
        }

        applyPostingVisibility(day);

        if (summaryPanel) {
            const scroll = document.querySelector('#message-container #conversation-container');
            if (scroll) {
                scroll.prepend(summaryPanel);
            }
        }
        ensureSummaryInsideScroll();

        const tid = document.querySelector('#message-container input[name="thread_id"]')?.value;
        if (window.NovaApp.messageManager) {
            window.NovaApp.messageManager.currentThreadId = tid || null;
            if (!state.reconnectChecked && typeof window.NovaApp.messageManager.checkAndReconnectRunningTasks === 'function') {
                state.reconnectChecked = true;
                window.NovaApp.messageManager.checkAndReconnectRunningTasks();
            }
        }

        if (!day) {
            if (window.NovaApp.messageManager && typeof window.NovaApp.messageManager.scrollToBottom === 'function') {
                window.NovaApp.messageManager.scrollToBottom();
            } else {
                const container = document.getElementById('conversation-container');
                if (container) {
                    container.scrollTop = container.scrollHeight;
                }
            }
        }

        document.dispatchEvent(new CustomEvent('threadChanged', { detail: { threadId: tid || null } }));
    }

    async function loadSummary(day) {
        const panel = document.getElementById('continuous-day-summary');
        const htmlEl = document.getElementById('continuous-summary-html');
        const titleEl = document.getElementById('continuous-day-summary-title');
        if (!panel || !htmlEl || !titleEl) return;

        if (!day || !state.isDayPinnedInUrl) {
            panel.style.display = 'none';
            return;
        }

        const summaryUrl = buildDaySummaryUrl(day);
        if (!summaryUrl) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = '';
        titleEl.textContent = `${t('Day Summary')} (${day})`;
        htmlEl.innerHTML = `<div class="text-muted">${t('Loading summary…')}</div>`;

        try {
            const resp = await fetch(summaryUrl, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
            });
            if (!resp.ok) {
                throw new Error(`summary_load_failed_${resp.status}`);
            }
            const data = await resp.json();
            htmlEl.innerHTML = data.summary_html || `<div class="text-muted">${t('No summary yet.')}</div>`;

            if (data.updated_at) {
                setSummaryEvent(`${t('Day summary updated')}: ${data.updated_at}`, 'muted');
            } else {
                setSummaryEvent('', 'muted');
            }
        } catch (err) {
            htmlEl.innerHTML = `<div class="text-danger">${t('Failed to load summary.')}</div>`;
            setSummaryEvent(t('Summary loading failed.'), 'danger');
        }

        ensureSummaryInsideScroll();
    }

    function closeSummarySocket() {
        if (state.summarySocket) {
            try {
                state.summarySocket.close();
            } catch (err) {
                // no-op
            }
        }
        state.summarySocket = null;
    }

    function watchSummaryTask(taskId, day) {
        closeSummarySocket();
        setRegenerateBusy(true);
        setSummaryEvent(t('Summary regeneration in progress…'), 'muted');

        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const wsUrl = `${protocol}://${window.location.host}/ws/task/${taskId}/`;
        const socket = new WebSocket(wsUrl);
        state.summarySocket = socket;

        socket.onmessage = async (event) => {
            let data = {};
            try {
                data = JSON.parse(event.data || '{}');
            } catch (err) {
                return;
            }
            if (data.type === 'progress_update') {
                setSummaryEvent(data.progress_log || t('Summary regeneration in progress…'), 'muted');
                return;
            }
            if (data.type === 'continuous_summary_ready') {
                scheduleDaysReload();
                if (state.selectedDay && data.day_label === state.selectedDay) {
                    await loadSummary(state.selectedDay);
                }
                if (data.updated_at) {
                    setSummaryEvent(`${t('Day summary updated')}: ${data.updated_at}`, 'success');
                }
                return;
            }
            if (data.type === 'task_complete') {
                scheduleDaysReload();
                if (state.selectedDay === day) {
                    await loadSummary(day);
                }
                setRegenerateBusy(false);
                closeSummarySocket();
                return;
            }
            if (data.type === 'task_error') {
                setSummaryEvent(data.message || t('Failed to regenerate summary.'), 'danger');
                setRegenerateBusy(false);
                closeSummarySocket();
            }
        };

        socket.onerror = () => {
            setSummaryEvent(t('Realtime status unavailable.'), 'danger');
        };

        socket.onclose = () => {
            if (state.summarySocket === socket) {
                state.summarySocket = null;
                setRegenerateBusy(false);
            }
        };
    }

    async function selectDay(day) {
        const normalizedDay = isValidDayLabel(day) ? day : null;
        setSelectedDay(normalizedDay);
        await loadMessages(normalizedDay);
        await loadSummary(normalizedDay);
    }

    async function loadOlderDays() {
        if (state.daysLoadingOlder || !state.daysHasMore) {
            return;
        }
        setLoadOlderBusy(true);
        await loadDays({ offset: state.daysOffset, append: true, withLoading: false, preserveScroll: true });
    }

    function bindDayClicks() {
        document.addEventListener('click', (e) => {
            const loadOlderBtn = e.target.closest('[data-action="continuous-load-older"]');
            if (loadOlderBtn) {
                e.preventDefault();
                loadOlderDays();
                return;
            }

            const dayLink = e.target.closest('[data-day-link="true"]');
            if (!dayLink) return;
            e.preventDefault();
            selectDay(dayLink.dataset.dayLabel);

            if (window.innerWidth < 992) {
                const ocEl = document.getElementById('threadsOffcanvas');
                if (ocEl && window.bootstrap && bootstrap.Offcanvas) {
                    bootstrap.Offcanvas.getOrCreateInstance(ocEl).hide();
                }
            }
        }, { capture: true });
    }

    function bindQuickActionButtons() {
        getLatestButtons().forEach((btn) => {
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                await selectDay(null);
            });
        });

        getTodayButtons().forEach((btn) => {
            btn.addEventListener('click', async (e) => {
                e.preventDefault();
                await selectDay(state.todayLabel);
            });
        });
    }

    function bindJumpInputs() {
        getJumpInputs().forEach((input) => {
            input.addEventListener('change', async (e) => {
                const value = e.target.value || '';
                setJumpInputValues(value);
                await selectDay(value);
            });
        });
    }

    function bindSearchInputs() {
        getSearchInputs().forEach((input) => {
            input.addEventListener('input', (e) => {
                const value = (e.target.value || '').trim();
                setDaysQuery(value, e.target);
                scheduleDaysSearchReload();
            });
        });
    }

    function bindDaysSidebarToggle() {
        const btn = getDaysToggleButton();
        if (!btn || btn._novaBoundDaysToggle) {
            return;
        }
        btn._novaBoundDaysToggle = true;
        btn.addEventListener('click', (e) => {
            e.preventDefault();
            toggleDaysSidebar();
        });
    }

    document.getElementById('continuous-load-days')?.addEventListener('click', (e) => {
        e.preventDefault();
        loadDays({ offset: 0, append: false, withLoading: false, preserveScroll: true });
    });

    document.getElementById('continuous-load-days-mobile')?.addEventListener('click', (e) => {
        e.preventDefault();
        loadDays({ offset: 0, append: false, withLoading: false, preserveScroll: true });
    });

    document.getElementById('continuous-regenerate-summary')?.addEventListener('click', async (e) => {
        e.preventDefault();
        if (!state.selectedDay || state.regenerateBusy) return;

        setRegenerateBusy(true);
        setSummaryEvent(t('Starting summary regeneration…'), 'muted');
        try {
            const body = new URLSearchParams({ day: state.selectedDay });
            const resp = await window.DOMUtils.csrfFetch(window.NovaApp.urls.regenerateSummary, {
                method: 'POST',
                headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                body
            });
            if (!resp.ok) {
                throw new Error(`regenerate_failed_${resp.status}`);
            }
            const data = await resp.json();
            if (!data.task_id) {
                throw new Error('missing_task_id');
            }
            watchSummaryTask(data.task_id, state.selectedDay);
        } catch (err) {
            setSummaryEvent(t('Failed to start summary regeneration.'), 'danger');
            setRegenerateBusy(false);
        }
    });

    document.addEventListener('nova:message-posted', (e) => {
        const detail = e.detail || {};
        if (detail.opened_new_day || detail.day_label) {
            scheduleDaysReload();
        }
    });

    (async function init() {
        bindDayClicks();
        bindQuickActionButtons();
        bindJumpInputs();
        bindSearchInputs();
        bindDaysSidebarToggle();

        state.daysVisible = readSavedDaysVisibility();
        applyDaysVisibility(state.daysVisible, { persist: false });

        await loadDays({ offset: 0, append: false, withLoading: true, preserveScroll: false });

        const initialDay = getSelectedDayFromUrl();
        if (initialDay) {
            await selectDay(initialDay);
        } else {
            setSelectedDay(null);
            await loadMessages(null);
            await loadSummary(null);
        }
    })();
})();
