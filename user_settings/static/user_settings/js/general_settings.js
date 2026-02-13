/* user_settings/static/user_settings/js/general_settings.js
 * Sync the numeric and range inputs for Continuous "latest messages" limit.
 * Works for both full-page and HTMX-loaded general settings forms.
 */
(() => {
  const NUMBER_INPUT_ID = "id_continuous_default_messages_limit";
  const RANGE_INPUT_ID = "id_continuous_default_messages_limit_range";

  function parseIntSafe(value) {
    const parsed = Number.parseInt(value, 10);
    return Number.isNaN(parsed) ? null : parsed;
  }

  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }

  function syncRangeFromNumber(numberInput, rangeInput, min, max) {
    const value = parseIntSafe(numberInput.value);
    if (value === null) {
      return;
    }
    rangeInput.value = String(clamp(value, min, max));
  }

  function normalizeNumber(numberInput, rangeInput, min, max) {
    const value = parseIntSafe(numberInput.value);
    if (value === null) {
      return;
    }
    const normalized = clamp(value, min, max);
    numberInput.value = String(normalized);
    rangeInput.value = String(normalized);
  }

  function bindContinuousLimitInputs(root = document) {
    const numberInput = root.querySelector(`#${NUMBER_INPUT_ID}`);
    const rangeInput = root.querySelector(`#${RANGE_INPUT_ID}`);
    if (!numberInput || !rangeInput) {
      return;
    }

    const min = parseIntSafe(numberInput.min) ?? parseIntSafe(rangeInput.min) ?? 10;
    const max = parseIntSafe(numberInput.max) ?? parseIntSafe(rangeInput.max) ?? 200;

    if (numberInput.dataset.rangeSyncBound === "1") {
      syncRangeFromNumber(numberInput, rangeInput, min, max);
      return;
    }

    numberInput.addEventListener("input", () => {
      syncRangeFromNumber(numberInput, rangeInput, min, max);
    });

    numberInput.addEventListener("change", () => {
      normalizeNumber(numberInput, rangeInput, min, max);
    });

    rangeInput.addEventListener("input", () => {
      numberInput.value = rangeInput.value;
      numberInput.dispatchEvent(new Event("input", { bubbles: true }));
    });

    numberInput.dataset.rangeSyncBound = "1";
    syncRangeFromNumber(numberInput, rangeInput, min, max);
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindContinuousLimitInputs(document);
  });

  document.body.addEventListener("htmx:afterSwap", () => {
    bindContinuousLimitInputs(document);
  });
})();
