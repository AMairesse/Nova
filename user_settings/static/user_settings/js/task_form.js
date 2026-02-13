/* Hybrid task cron helper (vanilla JS)
 * - Keeps a real cron text field (server-side validation remains source of truth)
 * - Provides lightweight presets (Bootstrap-native)
 * - Shows server-generated human-readable preview
 */

(function () {
    function byId(id) {
        return document.getElementById(id);
    }

    function parseJsonScript(id) {
        const el = byId(id);
        if (!el) return {};
        try {
            return JSON.parse(el.textContent || '{}');
        } catch (e) {
            return {};
        }
    }

    function selectedOptionText(selectEl) {
        if (!selectEl) return '';
        const idx = selectEl.selectedIndex;
        if (idx < 0) return '';
        const option = selectEl.options[idx];
        return option ? String(option.text || '').trim() : '';
    }

    function parseTimeToHM(value) {
        // HTML <input type="time"> usually returns "HH:MM"
        if (!value || typeof value !== 'string' || !value.includes(':')) return null;
        const [hStr, mStr] = value.split(':');
        const h = Number(hStr);
        const m = Number(mStr);
        if (!Number.isInteger(h) || !Number.isInteger(m)) return null;
        if (h < 0 || h > 23 || m < 0 || m > 59) return null;
        return { h, m };
    }

    function pad2(n) {
        return String(n).padStart(2, '0');
    }

    function formatTimeFromHM(h, m) {
        return `${pad2(h)}:${pad2(m)}`;
    }

    function isFivePartCron(expr) {
        const parts = String(expr || '').trim().split(/\s+/).filter(Boolean);
        return parts.length === 5;
    }

    function parseSimpleCron(expr) {
        // Returns a structured object for a few common patterns, otherwise null.
        const parts = String(expr || '').trim().split(/\s+/).filter(Boolean);
        if (parts.length !== 5) return null;

        const [min, hour, dom, mon, dow] = parts;

        // */N * * * *
        const everyN = /^\*\/(\d{1,2})$/.exec(min);
        if (everyN && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
            return { preset: 'every_n_minutes', everyMinutes: Number(everyN[1]) };
        }

        // 0 * * * *
        if (min === '0' && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
            return { preset: 'hourly' };
        }

        // M H * * *
        if (dom === '*' && mon === '*' && dow === '*' && /^\d{1,2}$/.test(min) && /^\d{1,2}$/.test(hour)) {
            return { preset: 'daily_at', time: formatTimeFromHM(Number(hour), Number(min)) };
        }

        // M H * * D
        if (dom === '*' && mon === '*' && /^\d$/.test(dow) && /^\d{1,2}$/.test(min) && /^\d{1,2}$/.test(hour)) {
            return {
                preset: 'weekly_at',
                time: formatTimeFromHM(Number(hour), Number(min)),
                weekday: dow,
            };
        }

        // M H DOM * *
        if (mon === '*' && dow === '*' && /^\d{1,2}$/.test(dom) && /^\d{1,2}$/.test(min) && /^\d{1,2}$/.test(hour)) {
            return {
                preset: 'monthly_at',
                time: formatTimeFromHM(Number(hour), Number(min)),
                monthday: dom,
            };
        }

        return null;
    }

    function setParamVisibility(container, preset) {
        const blocks = container.querySelectorAll('[data-cron-param]');
        let anyVisible = false;

        blocks.forEach((el) => {
            const tokens = el.getAttribute('data-cron-param') || '';
            const applies = tokens.split(/\s+/).includes(preset);
            el.hidden = !applies;
            if (applies) anyVisible = true;
        });

        container.hidden = !anyVisible;
    }

    function setup() {
        const form = byId('task_form');
        const cronInput = byId('id_cron_expression');
        const presetSelect = byId('cron_preset');
        const paramsRow = byId('cron_params');
        const triggerTypeInput = byId('id_trigger_type');
        const timezoneInput = byId('id_timezone');
        const agentInput = byId('id_agent');
        const promptInput = byId('id_prompt');
        const promptVariablesEmail = byId('prompt_variables_email');
        const runModeInput = byId('id_run_mode');
        const emailToolInput = byId('id_email_tool');
        const pollIntervalInput = byId('id_poll_interval_minutes');
        const emailToolAccessWarning = byId('email_tool_access_warning');
        const emailToolAccessWarningText = byId('email_tool_access_warning_text');
        const agentEmailToolIdsMap = parseJsonScript('agent_email_tool_ids_map');

        const everyMinutesInput = byId('cron_every_minutes');
        const timeInput = byId('cron_time');
        const weekdaySelect = byId('cron_weekday');
        const monthdayInput = byId('cron_monthday');

        const preview = byId('cron_preview');

        if (!form || !cronInput || !presetSelect || !paramsRow || !preview) return;

        const previewUrl = form.getAttribute('data-cron-preview-url');

        let isProgrammatic = false;
        let previewTimer = null;

        function fieldWrapper(el) {
            if (!el) return null;
            let cur = el;
            while (cur && cur !== document.body) {
                if (cur.classList && (cur.classList.contains('mb-3') || cur.classList.contains('form-group'))) {
                    return cur;
                }
                cur = cur.parentElement;
            }
            return el.parentElement;
        }

        function setFieldVisible(el, visible) {
            const wrapper = fieldWrapper(el);
            if (!wrapper) return;
            wrapper.style.display = visible ? '' : 'none';
        }

        function updateEmailToolAccessWarning() {
            if (!emailToolAccessWarning || !emailToolAccessWarningText) return;

            const trigger = triggerTypeInput ? triggerTypeInput.value : 'cron';
            const isEmailPoll = trigger === 'email_poll';
            if (!isEmailPoll) {
                emailToolAccessWarning.hidden = true;
                return;
            }

            const selectedAgentId = agentInput ? String(agentInput.value || '') : '';
            const selectedEmailToolId = emailToolInput ? String(emailToolInput.value || '') : '';
            if (!selectedAgentId || !selectedEmailToolId) {
                emailToolAccessWarning.hidden = true;
                return;
            }

            const allowedToolIds = Array.isArray(agentEmailToolIdsMap[selectedAgentId])
                ? agentEmailToolIdsMap[selectedAgentId].map((item) => String(item))
                : [];
            if (allowedToolIds.includes(selectedEmailToolId)) {
                emailToolAccessWarning.hidden = true;
                return;
            }

            const warningTemplate = emailToolAccessWarning.getAttribute('data-warning-template') || '';
            const selectedToolName = selectedOptionText(emailToolInput) || selectedEmailToolId;
            emailToolAccessWarningText.textContent = warningTemplate.replace('{tool}', selectedToolName);
            emailToolAccessWarning.hidden = false;
        }

        function applyTaskModeVisibility() {
            const trigger = triggerTypeInput ? triggerTypeInput.value : 'cron';
            const isEmailPoll = trigger === 'email_poll';
            const usesCron = trigger === 'cron';

            setFieldVisible(agentInput, true);
            setFieldVisible(promptInput, true);
            setFieldVisible(runModeInput, true);
            setFieldVisible(triggerTypeInput, true);
            if (promptVariablesEmail) {
                promptVariablesEmail.hidden = !isEmailPoll;
            }

            setFieldVisible(emailToolInput, isEmailPoll);
            setFieldVisible(pollIntervalInput, isEmailPoll);

            setFieldVisible(cronInput, usesCron);
            setFieldVisible(timezoneInput, usesCron);

            const cronHelper = byId('cron_helper');
            if (cronHelper) {
                cronHelper.style.display = usesCron ? '' : 'none';
            }

            updateEmailToolAccessWarning();
        }

        function renderPreviewState(state) {
            preview.classList.remove('text-danger', 'text-success', 'text-muted');

            if (!state) {
                preview.textContent = '';
                return;
            }

            if (state.loading) {
                preview.classList.add('text-muted');
                preview.textContent = state.message || '…';
                return;
            }

            if (state.valid) {
                preview.classList.add('text-success');
                preview.textContent = state.description || '';
                return;
            }

            preview.classList.add('text-danger');
            preview.textContent = state.error || '';
        }

        async function fetchPreview(expr) {
            if (!previewUrl) return;
            if (!expr) {
                renderPreviewState(null);
                return;
            }

            if (!isFivePartCron(expr)) {
                renderPreviewState({ valid: false, error: 'Cron must have 5 parts: minute hour day month weekday.' });
                return;
            }

            renderPreviewState({ loading: true, message: 'Validating…' });

            const url = new URL(previewUrl, window.location.origin);
            url.searchParams.set('cron_expression', expr);

            try {
                const res = await fetch(url.toString(), {
                    method: 'GET',
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest',
                    },
                    credentials: 'same-origin',
                });

                const data = await res.json();
                if (res.ok && data && data.valid) {
                    renderPreviewState({ valid: true, description: data.description });
                } else {
                    renderPreviewState({ valid: false, error: (data && data.error) || 'Invalid cron expression.' });
                }
            } catch (e) {
                renderPreviewState({ valid: false, error: 'Preview unavailable.' });
            }
        }

        function schedulePreview(expr) {
            if (!previewUrl) return;
            if (previewTimer) window.clearTimeout(previewTimer);
            previewTimer = window.setTimeout(() => {
                fetchPreview(expr);
            }, 250);
        }

        function setCron(expr) {
            isProgrammatic = true;
            cronInput.value = expr;
            cronInput.dispatchEvent(new Event('input', { bubbles: true }));
            cronInput.dispatchEvent(new Event('change', { bubbles: true }));
            isProgrammatic = false;
        }

        function applyPreset(preset) {
            setParamVisibility(paramsRow, preset);

            if (preset === 'custom') {
                schedulePreview(cronInput.value.trim());
                return;
            }

            if (preset === 'hourly') {
                setCron('0 * * * *');
                schedulePreview('0 * * * *');
                return;
            }

            if (preset === 'every_n_minutes') {
                const n = Number(everyMinutesInput && everyMinutesInput.value);
                const safeN = Number.isFinite(n) && n >= 1 && n <= 59 ? n : 5;
                const expr = `*/${safeN} * * * *`;
                setCron(expr);
                schedulePreview(expr);
                return;
            }

            if (preset === 'daily_at') {
                const hm = parseTimeToHM(timeInput && timeInput.value);
                const h = hm ? hm.h : 9;
                const m = hm ? hm.m : 0;
                const expr = `${m} ${h} * * *`;
                setCron(expr);
                schedulePreview(expr);
                return;
            }

            if (preset === 'weekly_at') {
                const hm = parseTimeToHM(timeInput && timeInput.value);
                const h = hm ? hm.h : 9;
                const m = hm ? hm.m : 0;
                const dow = weekdaySelect && weekdaySelect.value ? weekdaySelect.value : '1';
                const expr = `${m} ${h} * * ${dow}`;
                setCron(expr);
                schedulePreview(expr);
                return;
            }

            if (preset === 'monthly_at') {
                const hm = parseTimeToHM(timeInput && timeInput.value);
                const h = hm ? hm.h : 9;
                const m = hm ? hm.m : 0;
                const dom = monthdayInput && monthdayInput.value ? monthdayInput.value : '1';
                const expr = `${m} ${h} ${dom} * *`;
                setCron(expr);
                schedulePreview(expr);
                return;
            }
        }

        // --- Event bindings
        presetSelect.addEventListener('change', () => {
            applyPreset(presetSelect.value);
        });

        if (everyMinutesInput) {
            everyMinutesInput.addEventListener('input', () => {
                if (presetSelect.value === 'every_n_minutes') applyPreset('every_n_minutes');
            });
        }

        if (timeInput) {
            timeInput.addEventListener('input', () => {
                if (presetSelect.value === 'daily_at') applyPreset('daily_at');
                if (presetSelect.value === 'weekly_at') applyPreset('weekly_at');
                if (presetSelect.value === 'monthly_at') applyPreset('monthly_at');
            });
        }

        if (weekdaySelect) {
            weekdaySelect.addEventListener('change', () => {
                if (presetSelect.value === 'weekly_at') applyPreset('weekly_at');
            });
        }

        if (monthdayInput) {
            monthdayInput.addEventListener('input', () => {
                if (presetSelect.value === 'monthly_at') applyPreset('monthly_at');
            });
        }

        cronInput.addEventListener('input', () => {
            if (!isProgrammatic) {
                presetSelect.value = 'custom';
                setParamVisibility(paramsRow, 'custom');
            }
            schedulePreview(cronInput.value.trim());
        });

        if (triggerTypeInput) {
            triggerTypeInput.addEventListener('change', applyTaskModeVisibility);
        }
        if (agentInput) {
            agentInput.addEventListener('change', updateEmailToolAccessWarning);
        }
        if (emailToolInput) {
            emailToolInput.addEventListener('change', updateEmailToolAccessWarning);
        }

        // --- Initial state
        const initial = cronInput.value.trim();
        const parsed = parseSimpleCron(initial);
        if (parsed && parsed.preset) {
            presetSelect.value = parsed.preset;
            setParamVisibility(paramsRow, parsed.preset);

            if (parsed.preset === 'every_n_minutes' && everyMinutesInput && parsed.everyMinutes) {
                everyMinutesInput.value = String(parsed.everyMinutes);
            }
            if ((parsed.preset === 'daily_at' || parsed.preset === 'weekly_at' || parsed.preset === 'monthly_at') && timeInput && parsed.time) {
                timeInput.value = parsed.time;
            }
            if (parsed.preset === 'weekly_at' && weekdaySelect && parsed.weekday) {
                weekdaySelect.value = String(parsed.weekday);
            }
            if (parsed.preset === 'monthly_at' && monthdayInput && parsed.monthday) {
                monthdayInput.value = String(parsed.monthday);
            }
        } else {
            presetSelect.value = 'custom';
            setParamVisibility(paramsRow, 'custom');
        }

        schedulePreview(initial);
        applyTaskModeVisibility();
        updateEmailToolAccessWarning();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setup, { once: true });
    } else {
        setup();
    }
})();
