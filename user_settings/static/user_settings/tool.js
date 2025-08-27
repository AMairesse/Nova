/* user_settings/static/user_settings/tool.js */
document.addEventListener("DOMContentLoaded", () => {
  const typeSelect = document.querySelector('[name="tool_type"]');
  if (!typeSelect) return;

  const hideRow = (id) => {
    const row = document.getElementById(`div_id_${id}`);
    if (row) row.style.display = "none";
  };
  const showRow = (id, required = false) => {
    const row = document.getElementById(`div_id_${id}`);
    if (!row) return;
    row.style.display = "";
    if (required) {
      const inp = row.querySelector("input,select,textarea");
      if (inp) inp.required = true;
    }
  };

  const toggle = () => {
    ["name", "description", "tool_subtype", "endpoint", "transport_type", "input_schema", "output_schema"].forEach(hideRow);
    switch (typeSelect.value) {
      case "builtin":
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
