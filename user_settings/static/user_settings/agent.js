(function () {
  const isTool = document.querySelector("#id_is_tool");
  const wrapper = document.getElementById("tool-description-wrapper");
  if (!isTool || !wrapper) return;

  function sync() {
    wrapper.classList.toggle("d-none", !isTool.checked);
  }
  isTool.addEventListener("change", sync);
  sync();
})();
