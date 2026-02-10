// static/nova/js/view-preferences.js
(function () {
    'use strict';

    const MODE_THREADS = 'threads';
    const MODE_CONTINUOUS = 'continuous';
    const ROOT_THREADS_PATH = '/';
    const CONTINUOUS_PATH = '/continuous/';

    function getStorageKey() {
        if (window.StorageUtils && typeof window.StorageUtils.getLastViewModeKey === 'function') {
            return window.StorageUtils.getLastViewModeKey();
        }
        return 'nova:lastViewMode';
    }

    function getItem(key, defaultValue = null) {
        if (window.StorageUtils && typeof window.StorageUtils.getItem === 'function') {
            return window.StorageUtils.getItem(key, defaultValue);
        }
        try {
            const value = localStorage.getItem(key);
            return value !== null ? value : defaultValue;
        } catch (e) {
            return defaultValue;
        }
    }

    function setItem(key, value) {
        if (window.StorageUtils && typeof window.StorageUtils.setItem === 'function') {
            window.StorageUtils.setItem(key, value);
            return;
        }
        try {
            localStorage.setItem(key, value);
        } catch (e) {
            // no-op
        }
    }

    function normalizeMode(mode) {
        return mode === MODE_CONTINUOUS ? MODE_CONTINUOUS : MODE_THREADS;
    }

    function getLastMode() {
        return normalizeMode(getItem(getStorageKey(), MODE_THREADS));
    }

    function setLastMode(mode) {
        setItem(getStorageKey(), normalizeMode(mode));
    }

    function inferModeFromPath(pathname) {
        if (pathname.startsWith('/continuous')) {
            return MODE_CONTINUOUS;
        }
        if (pathname === ROOT_THREADS_PATH) {
            return MODE_THREADS;
        }
        return null;
    }

    function shouldRedirectToPreferredMode() {
        if (window.location.pathname !== ROOT_THREADS_PATH) {
            return false;
        }
        if (window.location.search || window.location.hash) {
            return false;
        }
        return getLastMode() === MODE_CONTINUOUS;
    }

    function bindModeLinkClicks() {
        document.querySelectorAll('a[data-view-mode]').forEach((el) => {
            if (el._novaBoundViewModePref) return;
            el._novaBoundViewModePref = true;
            el.addEventListener('click', () => {
                const mode = el.getAttribute('data-view-mode');
                if (mode === MODE_THREADS || mode === MODE_CONTINUOUS) {
                    setLastMode(mode);
                }
            });
        });
    }

    // PWA start_url is "/", so this also restores the last mode in standalone mode.
    if (shouldRedirectToPreferredMode()) {
        window.location.replace(CONTINUOUS_PATH);
    } else {
        // Persist mode choice before full app bootstrap.
        const currentMode = inferModeFromPath(window.location.pathname);
        if (currentMode) {
            setLastMode(currentMode);
        }
        document.addEventListener('DOMContentLoaded', bindModeLinkClicks);
    }

    window.NovaApp = window.NovaApp || {};
    window.NovaApp.viewPreferences = {
        getLastMode,
        setLastMode,
    };
})();
