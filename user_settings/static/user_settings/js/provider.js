/* user_settings/static/user_settings/js/provider.js */
document.addEventListener("DOMContentLoaded", function () {
  const select = document.getElementById("id_provider_type");
  const input = document.getElementById("id_api_key");
  const form = document.getElementById("provider-form");
  const actionInput = document.getElementById("provider-form-action");
  const saveButton = document.getElementById("save-provider-btn");
  const testButton = document.getElementById("test-provider-btn");

  if (select && input) {
    let wrapper = input.closest(".mb-3") || input.parentElement;
    const WITHOUT_KEY = new Set(["ollama", "lmstudio"]);

    function syncProviderTypeFields() {
      const needsKey = !WITHOUT_KEY.has(select.value);
      DOMUtils.toggleFieldVisibility(wrapper, needsKey);
      if (!needsKey) input.value = "";
    }

    select.addEventListener("change", syncProviderTypeFields);
    syncProviderTypeFields();
  }

  if (!form) return;

  let pollingTimerId = null;
  let pollingInFlight = false;
  const defaultTestButtonHtml = testButton ? testButton.innerHTML : "";

  function syncValidationUi() {
    const isTesting = form.dataset.validationStatus === "testing";
    if (!testButton) return;

    testButton.disabled = isTesting;
    if (isTesting) {
      testButton.innerHTML =
        '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' +
        gettext("Testing…");
      return;
    }

    testButton.innerHTML = defaultTestButtonHtml;
  }

  function startValidationPolling() {
    const pollUrl = form.dataset.validationStatusUrl;
    const pollIntervalMs = Number.parseInt(form.dataset.validationPollIntervalMs || "2000", 10);
    if (!pollUrl || form.dataset.validationStatus !== "testing" || pollingTimerId) {
      syncValidationUi();
      return;
    }

    const scheduleNextPoll = () => {
      pollingTimerId = window.setTimeout(runPoll, Number.isFinite(pollIntervalMs) ? pollIntervalMs : 2000);
    };

    const runPoll = async () => {
      if (pollingInFlight) {
        scheduleNextPoll();
        return;
      }
      pollingInFlight = true;

      try {
        const response = await fetch(pollUrl, {
          headers: {
            Accept: "application/json",
          },
          credentials: "same-origin",
        });
        if (!response.ok) {
          scheduleNextPoll();
          return;
        }

        const payload = await response.json();
        const status = payload.validation_status || "";
        form.dataset.validationStatus = status;
        syncValidationUi();

        if (status && status !== "testing") {
          window.location.reload();
          return;
        }

        scheduleNextPoll();
      } catch (_error) {
        scheduleNextPoll();
      } finally {
        pollingInFlight = false;
      }
    };

    syncValidationUi();
    scheduleNextPoll();
  }

  form.addEventListener("submit", function (event) {
    const submitter = event.submitter;
    if (!submitter) return;

    const submitAction = submitter.dataset.formAction || "";
    if (actionInput) {
      actionInput.value = submitAction;
    }

    const isValidationRun = submitAction === "test_provider";
    if (saveButton) saveButton.disabled = true;
    if (testButton) {
      testButton.disabled = true;
      if (isValidationRun) {
        testButton.dataset.originalHtml = testButton.innerHTML;
        testButton.innerHTML =
          '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>' +
          gettext("Testing…");
      }
    }
    if (isValidationRun) {
      form.dataset.validationStatus = "testing";
      syncValidationUi();
    }
  });

  syncValidationUi();
  startValidationPolling();
});
