/* user_settings/static/user_settings/js/tool.js */
document.addEventListener("DOMContentLoaded", () => {
  const kindSelect = document.querySelector('[name="connection_kind"]');
  if (!kindSelect) return;

  const hideRow = (id) => {
    DOMUtils.toggleFieldVisibility(`#div_id_${id}`, false);
  };
  const showRow = (id, required = false) => {
    DOMUtils.toggleFieldVisibility(`#div_id_${id}`, true, required);
  };

  const builtinKinds = new Set(["mail", "calendar", "webdav", "search", "python"]);

  const toggle = () => {
    ["name", "description", "endpoint", "transport_type"].forEach(hideRow);

    const value = `${kindSelect.value || ""}`.trim();
    if (!value) {
      return;
    }

    if (builtinKinds.has(value)) {
      showRow("name", true);
      return;
    }

    if (value === "api") {
      showRow("name", true);
      showRow("description", true);
      showRow("endpoint", true);
      return;
    }

    if (value === "mcp") {
      showRow("name", true);
      showRow("description", true);
      showRow("endpoint", true);
      showRow("transport_type");
    }
  };

  toggle();
  kindSelect.addEventListener("change", toggle);
});
