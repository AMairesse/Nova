(function () {
    'use strict';

    const root = document.getElementById('continuous-page-root');
    if (!root) {
        return;
    }

    const daySummaryUrlTemplate = root.dataset.urlDaySummary || '';

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
    };

    const t = (window.gettext && typeof window.gettext === 'function') ? window.gettext : (s) => s;

    const state = {
        selectedDay: null,
        isDayPinnedInUrl: false,
        todayLabel: getLocalISODate(),
        summarySocket: null,
        reconnectChecked: false,
        daysRefreshTimer: null,
        regenerateBusy: false,
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

    function getEffectiveDay() {
        return state.selectedDay || state.todayLabel;
    }

    function getSelectedDayFromUrl() {
        const qs = new URLSearchParams(window.location.search);
        return qs.get('day');
    }

    function buildDaySummaryUrl(day) {
        if (!daySummaryUrlTemplate || !day) {
            return '';
        }
        return daySummaryUrlTemplate.replace('__DAY__', encodeURIComponent(day));
    }

    function setSelectedDay(day) {
        state.selectedDay = day || null;
        state.isDayPinnedInUrl = !!state.selectedDay;
        const qs = new URLSearchParams(window.location.search);
        if (state.selectedDay) {
            qs.set('day', state.selectedDay);
        } else {
            qs.delete('day');
        }
        const nextUrl = `${window.location.pathname}${qs.toString() ? `?${qs.toString()}` : ''}`;
        window.history.replaceState({}, '', nextUrl);
        applyActiveDayInAllLists();
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
        container.querySelectorAll('a[data-day-label]').forEach((a) => {
            const isActive = !!activeDay && a.dataset.dayLabel === activeDay;
            a.classList.toggle('fw-semibold', isActive);
            a.classList.toggle('text-primary', isActive);
        });
    }

    function applyActiveDayInAllLists() {
        applyActiveDayInList('threads-list');
        applyActiveDayInList('mobile-threads-list');
    }

    async function loadDaysInto(containerId, { withLoading = true } = {}) {
        const el = document.getElementById(containerId);
        if (!el) return;
        if (withLoading) {
            el.innerHTML = `<div class="text-muted small p-3">${t('Loading days…')}</div>`;
        }
        try {
            const resp = await fetch(`${window.NovaApp.urls.loadDays}?offset=0&limit=30`, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            if (!resp.ok) {
                throw new Error(`HTTP ${resp.status}`);
            }
            const data = await resp.json();
            el.innerHTML = data.html || `<div class="text-muted small p-3">${t('No days yet.')}</div>`;
            applyActiveDayInList(containerId);
        } catch (err) {
            el.innerHTML = `<div class="text-danger small p-3">${t('Failed to load days.')}</div>`;
        }
    }

    function scheduleDaysReload() {
        if (state.daysRefreshTimer) {
            window.clearTimeout(state.daysRefreshTimer);
        }
        state.daysRefreshTimer = window.setTimeout(() => {
            loadDaysInto('threads-list', { withLoading: false });
            loadDaysInto('mobile-threads-list', { withLoading: false });
        }, 150);
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
        setSelectedDay(day);
        await loadMessages(day);
        await loadSummary(day);
    }

    function bindDayClicks() {
        document.addEventListener('click', (e) => {
            const a = e.target.closest('a[data-day-label]');
            if (!a) return;
            e.preventDefault();
            selectDay(a.dataset.dayLabel);

            if (window.innerWidth < 992) {
                const ocEl = document.getElementById('threadsOffcanvas');
                if (ocEl && window.bootstrap && bootstrap.Offcanvas) {
                    bootstrap.Offcanvas.getOrCreateInstance(ocEl).hide();
                }
            }
        }, { capture: true });
    }

    document.getElementById('continuous-load-days')?.addEventListener('click', (e) => {
        e.preventDefault();
        loadDaysInto('threads-list');
    });

    document.getElementById('continuous-load-days-mobile')?.addEventListener('click', (e) => {
        e.preventDefault();
        loadDaysInto('mobile-threads-list');
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
        await Promise.all([
            loadDaysInto('threads-list'),
            loadDaysInto('mobile-threads-list'),
        ]);

        const initialDay = getSelectedDayFromUrl();
        if (initialDay) {
            await selectDay(initialDay);
        } else {
            state.isDayPinnedInUrl = false;
            state.selectedDay = null;
            await loadMessages(state.todayLabel);
            await loadSummary(null);
            applyActiveDayInAllLists();
        }
    })();
})();
