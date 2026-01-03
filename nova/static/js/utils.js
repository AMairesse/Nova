// static/nova/js/utils.js
(function () {
  'use strict';

  // DOM Utilities
  window.DOMUtils = {
    // Get element by ID shorthand
    el: function (id) {
      return document.getElementById(id);
    },

    // Toggle field visibility using Bootstrap d-none class
    toggleFieldVisibility: function (selectorOrElement, visible, required = false) {
      let el;
      if (selectorOrElement instanceof Element) {
        el = selectorOrElement;
      } else {
        el = document.querySelector(selectorOrElement) || document.getElementById(selectorOrElement);
      }
      if (!el) return;
      el.classList.toggle('d-none', !visible);
      const input = el.querySelector('input,select,textarea');
      if (input) input.required = visible && required;
    },

    // Escape HTML for safe insertion
    escapeHTML: function (text) {
      return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&apos;');
    },

    // Escape attribute values
    escapeAttr: function (text) {
      return this.escapeHTML(text).replace(/`/g, '&#96;');
    },

    // CSRF-safe fetch wrapper
    csrfFetch: async function (url, options = {}) {
      const token = document.querySelector('[name=csrfmiddlewaretoken]');
      if (token) {
        options.headers = options.headers || {};
        options.headers['X-CSRFToken'] = token.value;
      }
      return fetch(url, options);
    },

    // Debounce function
    debounce: function (func, wait) {
      let timeout;
      return function executedFunction(...args) {
        const later = () => {
          clearTimeout(timeout);
          func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
      };
    }
  };

  // LocalStorage Utilities
  window.StorageUtils = {
    // Thread-related storage keys
    getThreadId: function () {
      return localStorage.getItem('lastThreadId') || null;
    },

    getWidthKey: function (threadId) {
      return `splitWidth:${threadId}`;
    },

    getSlugKey: function (threadId) {
      return `lastPreviewSlug:${threadId}`;
    },

    getSidebarTabKey: function (threadId) {
      return `sidebarTab:${threadId}`;
    },

    // Generic storage helpers
    getItem: function (key, defaultValue = null) {
      try {
        const item = localStorage.getItem(key);
        return item !== null ? item : defaultValue;
      } catch (e) {
        console.warn('localStorage access failed:', e);
        return defaultValue;
      }
    },

    setItem: function (key, value) {
      try {
        localStorage.setItem(key, value);
      } catch (e) {
        console.warn('localStorage write failed:', e);
      }
    }
  };

  // Event Utilities
  window.EventUtils = {
    // Add event listener with automatic cleanup tracking
    addListener: function (element, event, handler, options = {}) {
      element.addEventListener(event, handler, options);
      // Track for potential cleanup (optional)
      element._novaListeners = element._novaListeners || [];
      element._novaListeners.push({ event, handler });
    },

    // Remove all tracked listeners from element
    removeAllListeners: function (element) {
      if (!element._novaListeners) return;
      element._novaListeners.forEach(({ event, handler }) => {
        element.removeEventListener(event, handler);
      });
      element._novaListeners = [];
    }
  };

  // UI Utilities
  window.UIUtils = {
    // Show/hide spinner
    setSpinnerVisible: function (spinnerId, visible) {
      const spinner = DOMUtils.el(spinnerId);
      if (!spinner) return;
      spinner.classList.toggle('d-none', !visible);
      spinner.classList.toggle('d-flex', visible);
    },

    // Update progress bar
    updateProgressBar: function (barId, percentage, text = null) {
      const bar = DOMUtils.el(barId);
      if (!bar) return;
      bar.style.width = `${percentage}%`;
      bar.setAttribute('aria-valuenow', percentage);
      if (text !== null) {
        bar.textContent = text;
      }
    },

    // Check if mobile viewport
    isMobile: function () {
      return window.innerWidth < 992;
    }
  };

})();
