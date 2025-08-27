/* user_settings/static/user_settings/tool_configure.js */

document.addEventListener("DOMContentLoaded", () => {
  //------------------------------------------------------------
  // 1. Auth-type field switch (only for generic credentials)
  //------------------------------------------------------------
  const authSelect = document.querySelector('[name="auth_type"]');
  if (authSelect) {
    const mapping = {
      none: [],
      basic: ["username", "password"],
      token: ["token", "token_type"],
      api_key: ["token"],
      oauth: ["client_id", "client_secret"],
      custom: [],
    };
    const hideAll = () =>
      document.querySelectorAll("[data-auth-field]").forEach((c) => {
        c.style.display = "none";
        c.querySelectorAll("input").forEach((i) => (i.required = false));
      });
    const show = (names) =>
      names.forEach((n) => {
        const c = document.querySelector(`[data-auth-field="${n}"]`);
        if (c) {
          c.style.display = "";
          const i = c.querySelector("input");
          if (i) i.required = true;
        }
      });
    const toggle = () => {
      hideAll();
      show(mapping[authSelect.value] || []);
    };
    toggle();
    authSelect.addEventListener("change", toggle);
  }

  //------------------------------------------------------------
  // 2. Test connection
  //------------------------------------------------------------
  const testBtn = document.getElementById("testBtn");
  if (testBtn) {
    const resultBox = document.getElementById("testResult");
    testBtn.addEventListener("click", async () => {
      resultBox.className = "alert alert-info";
      resultBox.textContent = "Testing…";
      resultBox.classList.remove("d-none");

      const url = testBtn.dataset.testUrl;
      const formData = new FormData(document.getElementById("configForm"));

      try {
        const resp = await fetch(url, {
          method: "POST",
          headers: { "X-CSRFToken": formData.get("csrfmiddlewaretoken") },
          body: formData,
        });
        const data = await resp.json();
        resultBox.textContent = data.message || data.status;
        resultBox.className =
          "alert " + (data.status === "success" ? "alert-success" : "alert-danger");
      } catch (e) {
        resultBox.textContent = e;
        resultBox.className = "alert alert-danger";
      }
    });
  }
});
