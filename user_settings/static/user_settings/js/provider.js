/* user_settings/static/user_settings/js/provider.js */
document.addEventListener("DOMContentLoaded", function () {
  const select = document.getElementById("id_provider_type");
  const input = document.getElementById("id_api_key");

  if (!select || !input) return;

  // wrapper is the closest crispy “div.mb-3” if it exists
  let wrapper = input.closest(".mb-3") || input.parentElement;

  const WITHOUT_KEY = new Set(["ollama", "lmstudio"]);

  function sync() {
    const needsKey = !WITHOUT_KEY.has(select.value);
    if (wrapper) wrapper.classList.toggle("d-none", !needsKey);
    if (!needsKey) input.value = "";
  }

  select.addEventListener("change", sync);
  sync(); // initial state on page load
});
