/* nova/static/js/utils.js - Utility functions */
(function() {
  'use strict';

  // LocalStorage utilities with expiration support
  const StorageUtils = {
    // Set item with expiry (TTL in ms, default 1h)
    setWithExpiry(key, value, ttl = 3600000) {
      const now = new Date().getTime();
      const item = { value: value, expiry: now + ttl };
      localStorage.setItem(key, JSON.stringify(item));
    },

    // Get item and check expiry
    getWithExpiry(key) {
      const itemStr = localStorage.getItem(key);
      if (!itemStr) return null;
      
      try {
        const item = JSON.parse(itemStr);
        const now = new Date().getTime();
        if (now > item.expiry) {
          localStorage.removeItem(key);
          return null;
        }
        return item.value;
      } catch {
        localStorage.removeItem(key);
        return null;
      }
    },

    // Simple get/set without expiry
    set(key, value) {
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (error) {
        console.warn('Failed to save to localStorage:', error);
      }
    },

    get(key) {
      try {
        const item = localStorage.getItem(key);
        return item ? JSON.parse(item) : null;
      } catch {
        return null;
      }
    },

    remove(key) {
      localStorage.removeItem(key);
    },

    // Task-specific helpers
    addStoredTask(threadId, taskId) {
      if (!threadId || !taskId) return;
      const key = `storedTask_${threadId}`;
      this.setWithExpiry(key, taskId);
    },

    removeStoredTask(threadId, taskId) {
      const key = `storedTask_${threadId}`;
      const storedTask = this.getWithExpiry(key);
      if (storedTask === taskId) {
        this.remove(key);
      }
    },

    getStoredRunningTasks(threadId) {
      return this.get(`runningTasks_${threadId}`) || [];
    },

    setStoredRunningTasks(threadId, taskIds) {
      this.set(`runningTasks_${threadId}`, taskIds);
    }
  };

  // DOM utilities to replace jQuery
  const DOMUtils = {
    // Query selectors
    $(selector) {
      return document.querySelector(selector);
    },

    $$(selector) {
      return document.querySelectorAll(selector);
    },

    // Event handling
    on(element, event, handler) {
      if (typeof element === 'string') {
        element = this.$(element);
      }
      if (element) {
        element.addEventListener(event, handler);
      }
    },

    // Form data serialization
    serializeForm(form) {
      if (typeof form === 'string') {
        form = this.$(form);
      }
      return new FormData(form);
    },

    // Simple AJAX wrapper
    async ajax(options) {
      const {
        url,
        method = 'GET',
        data,
        headers = {}
      } = options;

      const config = {
        method,
        headers: {
          'X-AJAX': 'true',
          ...headers
        }
      };

      if (data) {
        if (data instanceof FormData) {
          config.body = data;
        } else if (typeof data === 'object') {
          config.headers['Content-Type'] = 'application/json';
          config.body = JSON.stringify(data);
        } else {
          config.body = data;
        }
      }

      const response = await fetch(url, config);
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const contentType = response.headers.get('content-type');
      if (contentType && contentType.includes('application/json')) {
        return response.json();
      }
      return response.text();
    }
  };

  // Expose utilities globally
  window.StorageUtils = StorageUtils;
  window.DOMUtils = DOMUtils;

})();
