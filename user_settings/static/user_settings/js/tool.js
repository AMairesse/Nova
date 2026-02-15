/* user_settings/static/user_settings/js/tool.js */
document.addEventListener("DOMContentLoaded", () => {
  const typeSelect = document.querySelector('[name="tool_type"]');
  if (!typeSelect) return;

  const hideRow = (id) => {
    DOMUtils.toggleFieldVisibility(`#div_id_${id}`, false);
  };
  const showRow = (id, required = false) => {
    DOMUtils.toggleFieldVisibility(`#div_id_${id}`, true, required);
  };

  const toggle = () => {
    ["name", "description", "tool_subtype", "endpoint", "transport_type", "input_schema", "output_schema"].forEach(hideRow);
    switch (typeSelect.value) {
      case "builtin":
        showRow("name", true);
        showRow("tool_subtype", true);
        break;
      case "api":
        showRow("name", true);
        showRow("description", true);
        showRow("endpoint", true);
        showRow("input_schema");
        showRow("output_schema");
        break;
      case "mcp":
        showRow("name", true);
        showRow("description", true);
        showRow("endpoint", true);
        showRow("transport_type");
        break;
      default:
        break;
    }
  };
  toggle();
  typeSelect.addEventListener("change", toggle);
});
