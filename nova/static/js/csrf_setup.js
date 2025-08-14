// nova/static/js/csrf_setup.js
// Fetch-based helper for CSRF + HttpOnly cookies (vanilla, no jQuery)
(() => {
  /* Cache the promise so we ne­go­ti­ate the token only once per tab */
  let tokenPromise = null;

  async function getCSRFToken() {
    if (!tokenPromise) {
      tokenPromise = fetch("/api/csrf/", { credentials: "include" })
        .then(r => r.json())
        .then(({ csrfToken }) => csrfToken);
    }
    return tokenPromise;
  }

  /* Drop-in replacement for fetch() that auto-adds X-CSRFToken */
  async function csrfFetch(input, init = {}) {
    const method = (init.method || "GET").toUpperCase();
    const headers = new Headers(init.headers || {});

    if (!/^(GET|HEAD|OPTIONS|TRACE)$/.test(method)) {
      headers.set("X-CSRFToken", await getCSRFToken());
    }

    return fetch(input, {
      ...init,
      method,
      headers,
      credentials: "include",   // keep cookies by default
    });
  }

  // Expose helpers globally (adjust namespace if you prefer modules)
  window.getCSRFToken = getCSRFToken;
  window.csrfFetch    = csrfFetch;
})();
