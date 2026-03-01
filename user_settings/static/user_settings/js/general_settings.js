/* user_settings/static/user_settings/js/general_settings.js
 * General settings UI behaviors:
 * - Continuous "latest messages" number/range synchronization
 * - Push notification activation flow from Settings (button + switch)
 * Works for both full-page and HTMX-loaded fragments.
 */
(() => {
  const NUMBER_INPUT_ID = "id_continuous_default_messages_limit";
  const RANGE_INPUT_ID = "id_continuous_default_messages_limit_range";
  const NOTIFICATIONS_CONTAINER_ID = "task-notifications-controls";
  const NOTIFICATIONS_FIELD_ID = "id_task_notifications_enabled";
  const NOTIFICATIONS_ENABLE_BUTTON_ID = "task-notifications-enable-device-btn";
  const NOTIFICATIONS_STATUS_ID = "task-notifications-device-status";
  const NOTIFICATIONS_MESSAGE_ID = "task-notifications-device-message";

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

  async function ensureNotificationManagerReady() {
    if (!window.NotificationManager) {
      return null;
    }

    if (!window.NotificationManager.registration && "serviceWorker" in navigator) {
      try {
        const registration = await navigator.serviceWorker.getRegistration("/");
        if (registration) {
          window.NotificationManager.registration = registration;
        }
      } catch (_error) {
        // No-op: status UI will indicate unavailable state.
      }
    }

    try {
      const config = await window.NotificationManager.fetchConfig();
      if (config) {
        window.NotificationManager.config = config;
      }
    } catch (_error) {
      // Keep existing config if any.
    }

    return window.NotificationManager;
  }

  function setStatusBadge(target, value, tone = "secondary") {
    if (!target) return;
    target.classList.remove(
      "text-bg-secondary",
      "text-bg-success",
      "text-bg-warning",
      "text-bg-danger",
      "text-bg-info"
    );
    target.classList.add(`text-bg-${tone}`);
    target.textContent = value || "";
  }

  function setMessage(target, value, level = "muted") {
    if (!target) return;
    target.classList.remove("text-muted", "text-success", "text-warning", "text-danger");
    target.classList.add(`text-${level}`);
    target.textContent = value || "";
  }

  async function readNotificationRuntimeState(manager, serverState) {
    const supported = (
      "Notification" in window &&
      "serviceWorker" in navigator &&
      "PushManager" in window
    );
    const permission = supported ? Notification.permission : "unsupported";

    let hasSubscription = false;
    if (supported && manager?.registration) {
      try {
        const subscription = await manager.registration.pushManager.getSubscription();
        hasSubscription = Boolean(subscription);
      } catch (_error) {
        hasSubscription = false;
      }
    }

    let statusText = "Browser permission not requested.";
    if (!supported) {
      statusText = "Push notifications are not supported in this browser.";
    } else if (serverState !== "ready") {
      statusText = "Server-side Web Push is not available.";
    } else if (permission === "denied") {
      statusText = "Browser permission denied.";
    } else if (permission === "granted" && hasSubscription) {
      statusText = "This device/browser is subscribed.";
    } else if (permission === "granted") {
      statusText = "Browser permission granted (not yet subscribed).";
    }

    return {
      supported,
      permission,
      hasSubscription,
      statusText,
    };
  }

  async function refreshNotificationsControls(container) {
    if (!container) return null;
    const checkbox = container.querySelector(`#${NOTIFICATIONS_FIELD_ID}`);
    const button = container.querySelector(`#${NOTIFICATIONS_ENABLE_BUTTON_ID}`);
    const status = container.querySelector(`#${NOTIFICATIONS_STATUS_ID}`);
    if (!checkbox || !button || !status) {
      return null;
    }

    const serverState = container.dataset.serverState || "disabled";
    const manager = await ensureNotificationManagerReady();
    const runtimeState = await readNotificationRuntimeState(manager, serverState);

    button.disabled = checkbox.disabled || serverState !== "ready" || !runtimeState.supported;
    let badgeTone = "secondary";
    if (!runtimeState.supported || serverState !== "ready") {
      badgeTone = "warning";
    } else if (runtimeState.permission === "denied") {
      badgeTone = "danger";
    } else if (runtimeState.permission === "granted" && runtimeState.hasSubscription) {
      badgeTone = "success";
    } else if (runtimeState.permission === "granted") {
      badgeTone = "info";
    }
    setStatusBadge(status, runtimeState.statusText, badgeTone);
    return { manager, runtimeState, serverState, checkbox };
  }

  async function activateNotificationsOnDevice(container, { fromToggle = false } = {}) {
    const message = container.querySelector(`#${NOTIFICATIONS_MESSAGE_ID}`);
    const state = await refreshNotificationsControls(container);
    if (!state) return;

    const { manager, runtimeState, serverState, checkbox } = state;
    if (serverState !== "ready") {
      if (fromToggle) checkbox.checked = false;
      setMessage(message, "Server-side Web Push is disabled or incomplete.", "warning");
      return;
    }

    if (!runtimeState.supported || !manager) {
      if (fromToggle) checkbox.checked = false;
      setMessage(message, "Push notifications are not supported in this browser.", "warning");
      return;
    }

    if (!manager.config || manager.config.server_state !== "ready") {
      if (fromToggle) checkbox.checked = false;
      setMessage(message, "Server push configuration is not ready.", "warning");
      return;
    }

    if (runtimeState.permission === "denied") {
      if (fromToggle) checkbox.checked = false;
      setMessage(message, "Notifications are blocked in browser settings.", "danger");
      return;
    }

    let subscribed = false;
    if (runtimeState.permission === "granted") {
      subscribed = await manager.syncSubscription();
    } else {
      subscribed = await manager.requestPermissionAndSubscribe();
    }

    if (!subscribed) {
      if (fromToggle) checkbox.checked = false;
      const denied = "Notification" in window && Notification.permission === "denied";
      setMessage(
        message,
        denied
          ? "Notifications are blocked in browser settings."
          : "Notification permission was not granted.",
        denied ? "danger" : "warning"
      );
      await refreshNotificationsControls(container);
      return;
    }

    checkbox.checked = true;
    setMessage(
      message,
      "Device/browser notifications enabled. Click Save Settings to persist.",
      "success"
    );
    await refreshNotificationsControls(container);
  }

  async function deactivateNotificationsOnDevice(container) {
    const message = container.querySelector(`#${NOTIFICATIONS_MESSAGE_ID}`);
    const state = await refreshNotificationsControls(container);
    if (!state) return;

    const { manager } = state;
    if (manager) {
      await manager.unsubscribe();
    }
    setMessage(
      message,
      "Device/browser notifications disabled. Click Save Settings to persist.",
      "muted"
    );
    await refreshNotificationsControls(container);
  }

  function bindTaskNotificationsControls(root = document) {
    const container = root.querySelector(`#${NOTIFICATIONS_CONTAINER_ID}`);
    if (!container) {
      return;
    }

    const checkbox = container.querySelector(`#${NOTIFICATIONS_FIELD_ID}`);
    const button = container.querySelector(`#${NOTIFICATIONS_ENABLE_BUTTON_ID}`);
    if (!checkbox || !button) {
      return;
    }

    if (checkbox.dataset.pushControlsBound === "1") {
      refreshNotificationsControls(container);
      return;
    }

    button.addEventListener("click", async () => {
      await activateNotificationsOnDevice(container);
    });

    checkbox.addEventListener("change", async () => {
      if (checkbox.checked) {
        await activateNotificationsOnDevice(container, { fromToggle: true });
      } else {
        await deactivateNotificationsOnDevice(container);
      }
    });

    checkbox.dataset.pushControlsBound = "1";
    refreshNotificationsControls(container);
  }

  document.addEventListener("DOMContentLoaded", () => {
    bindContinuousLimitInputs(document);
    bindTaskNotificationsControls(document);
  });

  document.body.addEventListener("htmx:afterSwap", () => {
    bindContinuousLimitInputs(document);
    bindTaskNotificationsControls(document);
  });
})();
