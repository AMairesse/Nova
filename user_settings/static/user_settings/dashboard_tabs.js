/* dashboard_tabs.js
 * Activate the correct Bootstrap tab when the page loads
 * and keep the location.hash in sync when the user navigates.
 */
document.addEventListener("DOMContentLoaded", () => {
  const hash = window.location.hash;

  // 1) Activate tab on load if a matching hash exists
  if (hash.startsWith("#pane-")) {
    const trigger = document.querySelector(
      `[data-bs-toggle="tab"][data-bs-target="${hash}"]`
    );
    if (trigger) {
      const tab = new bootstrap.Tab(trigger);
      tab.show();
    }
  }

  // 2) Keep the URL hash in sync when the user clicks on tabs
  const tabTriggers = document.querySelectorAll('[data-bs-toggle="tab"]');
  tabTriggers.forEach((el) => {
    el.addEventListener("shown.bs.tab", (event) => {
      const target = event.target.getAttribute("data-bs-target");
      if (target) {
        history.replaceState(null, "", target);
      }
    });
  });
});
