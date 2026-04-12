/* user_settings/static/user_settings/js/dashboard_tabs.js
 * Keep the dashboard tabs and location hash in sync, including
 * links rendered inside HTMX panes such as the Memory shortcut
 * from the Tools tab.
 */
document.addEventListener("DOMContentLoaded", () => {
  const tabTriggers = Array.from(
    document.querySelectorAll('[data-bs-toggle="tab"][data-bs-target^="#pane-"]')
  );

  function findTabTrigger(hash) {
    return tabTriggers.find((trigger) => trigger.getAttribute("data-bs-target") === hash);
  }

  function loadPaneIfNeeded(hash) {
    const pane = document.querySelector(hash);
    if (!pane || !window.htmx || !pane.hasAttribute("hx-get")) {
      return;
    }
    window.htmx.trigger(pane, "settings-pane:load");
  }

  function showTabForHash(hash) {
    if (!hash || !hash.startsWith("#pane-") || !window.bootstrap?.Tab) {
      return false;
    }

    const trigger = findTabTrigger(hash);
    if (!trigger) {
      return false;
    }

    window.bootstrap.Tab.getOrCreateInstance(trigger).show();
    return true;
  }

  function isSameDashboardLocation(url) {
    return (
      url.origin === window.location.origin &&
      url.pathname === window.location.pathname
    );
  }

  if (window.location.hash) {
    showTabForHash(window.location.hash);
  }

  window.addEventListener("hashchange", () => {
    showTabForHash(window.location.hash);
  });

  document.addEventListener("click", (event) => {
    const link = event.target.closest('a[href*="#pane-"]');
    if (!link) {
      return;
    }

    const url = new URL(link.getAttribute("href"), window.location.href);
    if (!isSameDashboardLocation(url) || !url.hash.startsWith("#pane-")) {
      return;
    }

    event.preventDefault();
    if (showTabForHash(url.hash)) {
      history.replaceState(null, "", url.hash);
    }
  });

  tabTriggers.forEach((el) => {
    el.addEventListener("shown.bs.tab", (event) => {
      const target = event.target.getAttribute("data-bs-target");
      if (target) {
        history.replaceState(null, "", target);
        loadPaneIfNeeded(target);
      }
    });
  });
});
