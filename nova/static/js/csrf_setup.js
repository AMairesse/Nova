// nova/static/js/csrf_setup.js
// Global helper that works with HttpOnly cookies
(async function () {
  async function fetchToken() {
    const r = await fetch("/api/csrf/", { credentials: "include" });
    const { csrfToken } = await r.json();
    return csrfToken;
  }

  window.getCSRFToken = fetchToken;  // Expose async getter (for manual use)

  /* jQuery integration: fetch per request (no global cache) */
  if (typeof jQuery !== "undefined") {
    jQuery.ajaxSetup({
      beforeSend: async function (xhr, settings) {
        if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type)) {
          const token = await fetchToken();  // Fresh token per request
          xhr.setRequestHeader("X-CSRFToken", token);
        }
      },
    });
  }
})();
