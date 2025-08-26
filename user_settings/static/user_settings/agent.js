(function () {
  const isTool = document.querySelector("#id_is_tool");
  const descRow = document.querySelector("#id_tool_description")?.closest(".mb-3");

  if (!isTool || !descRow) return;

  function sync() {
    descRow.classList.toggle("d-none", !isTool.checked);
  }

  isTool.addEventListener("change", sync);
  sync(); // initial state
})();
