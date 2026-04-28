from __future__ import annotations

import os
from unittest import SkipTest

from django.conf import settings
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


_FAKE_BROWSER_RUNTIME = """
(() => {
  class FakeWebSocket {
    static CONNECTING = 0;
    static OPEN = 1;
    static CLOSING = 2;
    static CLOSED = 3;
    static instances = [];

    constructor(url) {
      this.url = url;
      this.readyState = FakeWebSocket.CONNECTING;
      this.sentMessages = [];
      this.onopen = null;
      this.onmessage = null;
      this.onerror = null;
      this.onclose = null;
      FakeWebSocket.instances.push(this);

      queueMicrotask(() => {
        this.readyState = FakeWebSocket.OPEN;
        if (typeof this.onopen === "function") {
          this.onopen({ target: this, type: "open" });
        }
      });
    }

    send(payload) {
      this.sentMessages.push(payload);
    }

    close(code = 1000, reason = "") {
      this.readyState = FakeWebSocket.CLOSED;
      if (typeof this.onclose === "function") {
        this.onclose({
          code,
          reason,
          target: this,
          type: "close",
          wasClean: true,
        });
      }
    }

    _dispatch(payload) {
      if (typeof this.onmessage === "function") {
        this.onmessage({ data: JSON.stringify(payload), target: this, type: "message" });
      }
    }
  }

  class FakeSpeechRecognition {
    static instances = [];

    constructor() {
      this.continuous = false;
      this.interimResults = false;
      this.lang = "en-US";
      this.onstart = null;
      this.onresult = null;
      this.onerror = null;
      this.onend = null;
      FakeSpeechRecognition.instances.push(this);
    }

    start() {
      if (typeof this.onstart === "function") {
        this.onstart({ target: this, type: "start" });
      }
    }

    stop() {
      if (typeof this.onend === "function") {
        this.onend({ target: this, type: "end" });
      }
    }

    abort() {
      if (typeof this.onend === "function") {
        this.onend({ target: this, type: "end" });
      }
    }

    _dispatchResult({ transcript, isFinal = true }) {
      const results = [
        {
          0: { transcript },
          isFinal,
          length: 1,
        },
      ];
      if (typeof this.onresult === "function") {
        this.onresult({
          resultIndex: 0,
          results,
          target: this,
          type: "result",
        });
      }
    }

    _dispatchError(error) {
      if (typeof this.onerror === "function") {
        this.onerror({ error, target: this, type: "error" });
      }
    }

    _dispatchEnd() {
      if (typeof this.onend === "function") {
        this.onend({ target: this, type: "end" });
      }
    }
  }

  window.SpeechRecognition = FakeSpeechRecognition;
  window.webkitSpeechRecognition = FakeSpeechRecognition;
  window.WebSocket = FakeWebSocket;
  window.__novaTest = window.__novaTest || {};
  window.__novaTest.clipboardText = "";
  window.__novaTest.getSocketUrls = () => FakeWebSocket.instances.map((socket) => socket.url);
  window.__novaTest.pushTaskEvent = (taskId, payload) => {
    const suffix = `/ws/task/${taskId}/`;
    const socket = FakeWebSocket.instances.find((entry) => String(entry.url).includes(suffix));
    if (!socket) {
      return false;
    }
    socket._dispatch(payload);
    return true;
  };
  window.__novaTest.pushSpeechResult = ({ transcript, isFinal = true }) => {
    const instance = FakeSpeechRecognition.instances.at(-1);
    if (!instance) {
      return false;
    }
    instance._dispatchResult({ transcript, isFinal });
    return true;
  };
  window.__novaTest.pushSpeechError = (error) => {
    const instance = FakeSpeechRecognition.instances.at(-1);
    if (!instance) {
      return false;
    }
    instance._dispatchError(error);
    return true;
  };
  window.__novaTest.endSpeech = () => {
    const instance = FakeSpeechRecognition.instances.at(-1);
    if (!instance) {
      return false;
    }
    instance._dispatchEnd();
    return true;
  };
  window.__novaTest.getClipboardText = () => window.__novaTest.clipboardText || "";

  try {
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: {
        writeText: async (text) => {
          window.__novaTest.clipboardText = String(text);
        },
      },
    });
  } catch (error) {
    // Ignore environments where navigator.clipboard cannot be redefined.
  }

  try {
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: {
        register: async () => ({
          scope: "",
          active: { postMessage() {} },
          update() {
            return Promise.resolve();
          },
        }),
      },
    });
  } catch (error) {
    // Ignore environments where navigator.serviceWorker cannot be redefined.
  }

  try {
    const fakeMediaDevices = {
      getUserMedia: async () => ({
        getTracks() {
          return [];
        },
      }),
    };
    if (navigator.mediaDevices) {
      navigator.mediaDevices.getUserMedia = fakeMediaDevices.getUserMedia;
    } else {
      Object.defineProperty(navigator, "mediaDevices", {
        configurable: true,
        value: fakeMediaDevices,
      });
    }
  } catch (error) {
    // Ignore environments where navigator.mediaDevices cannot be redefined.
  }
})();
"""


class PlaywrightLiveServerTestCase(StaticLiveServerTestCase):
    browser = None
    playwright = None
    _previous_async_unsafe = None

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._previous_async_unsafe = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
        os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            if cls.playwright is not None:
                cls.playwright.stop()
                cls.playwright = None
            raise SkipTest(
                "Playwright Chromium is not available. Run `playwright install chromium`."
            ) from exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser is not None:
            cls.browser.close()
            cls.browser = None
        if cls.playwright is not None:
            cls.playwright.stop()
            cls.playwright = None
        if cls._previous_async_unsafe is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = cls._previous_async_unsafe
        cls._previous_async_unsafe = None
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self._create_browser_context()
        self.addCleanup(self._close_browser_context)

    def _create_browser_context(
        self,
        *,
        viewport: dict | None = None,
        has_touch: bool = False,
        is_mobile: bool = False,
        extra_init_scripts: list[str] | None = None,
        init_scripts: list[str] | None = None,
    ):
        self.context = self.browser.new_context(
            viewport=viewport or {"width": 1440, "height": 960},
            has_touch=has_touch,
            is_mobile=is_mobile,
        )
        self.context.add_init_script(_FAKE_BROWSER_RUNTIME)
        scripts = list(extra_init_scripts or []) + list(init_scripts or [])
        for script in scripts:
            self.context.add_init_script(script)
        self.page = self.context.new_page()

    def recreate_browser_context(self, **kwargs):
        self._close_browser_context()
        self._create_browser_context(**kwargs)

    def _close_browser_context(self):
        if getattr(self, "page", None) is not None:
            self.page.close()
            self.page = None
        if getattr(self, "context", None) is not None:
            self.context.close()
            self.context = None

    def login_to_browser(self, user):
        self.client.force_login(user)
        session_cookie = self.client.cookies[settings.SESSION_COOKIE_NAME]
        self.context.add_cookies(
            [
                {
                    "name": settings.SESSION_COOKIE_NAME,
                    "value": session_cookie.value,
                    "url": self.live_server_url,
                    "httpOnly": True,
                    "sameSite": "Lax",
                }
            ]
        )

    def open_path(self, path: str = "/"):
        self.page.goto(
            f"{self.live_server_url}{path}",
            wait_until="domcontentloaded",
        )
        return self.page

    def push_task_event(self, task_id: int, payload: dict) -> bool:
        return bool(
            self.page.evaluate(
                """
                ({ taskId, payload }) => window.__novaTest.pushTaskEvent(taskId, payload)
                """,
                {"taskId": int(task_id), "payload": payload},
            )
        )

    def push_speech_result(self, transcript: str, *, is_final: bool = True) -> bool:
        return bool(
            self.page.evaluate(
                """
                ({ transcript, isFinal }) =>
                    window.__novaTest.pushSpeechResult({ transcript, isFinal })
                """,
                {"transcript": transcript, "isFinal": is_final},
            )
        )

    def end_speech(self) -> bool:
        return bool(self.page.evaluate("() => window.__novaTest.endSpeech()"))

    def get_clipboard_text(self) -> str:
        return str(self.page.evaluate("() => window.__novaTest.getClipboardText()"))
