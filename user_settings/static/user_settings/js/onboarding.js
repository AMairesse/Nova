(function () {
    'use strict';

    const card = document.getElementById('nova-onboarding');
    if (!card) return;
    const storageKey = `nova:onboarding:v1:${document.body?.dataset?.userId || 'anonymous'}`;
    const body = card.querySelector('.card-body');
    const toggle = card.querySelector('[data-onboarding-toggle]');
    const setCollapsed = (collapsed) => {
        body?.classList.toggle('d-none', collapsed);
        if (toggle) toggle.textContent = collapsed ? gettext('Resume') : gettext('Hide');
    };
    try { setCollapsed(window.localStorage.getItem(storageKey) === 'collapsed'); } catch (_error) {}

    card.addEventListener('click', (event) => {
        if (!event.target.closest('[data-onboarding-toggle]')) return;
        const collapsed = !body?.classList.contains('d-none');
        setCollapsed(collapsed);
        try { window.localStorage.setItem(storageKey, collapsed ? 'collapsed' : 'open'); } catch (_error) {}
    });
})();
