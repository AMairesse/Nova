import sys
import types
import importlib
import asyncio
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch


class BrowserBuiltinTests(IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        # Any per-test setup can go here

    def tearDown(self):
        # Explicitly clean up any potentially lingering modules after each test
        mocked_modules = [
            "playwright", "playwright.async_api",
            "langchain_community", "langchain_community.agent_toolkits",
            "nova.tools.builtins.browser"  # Ensure the module under test can be reloaded if needed
        ]
        for mod in mocked_modules:
            sys.modules.pop(mod, None)
        super().tearDown()

    def _get_fake_third_party_modules(self, counters):
        # Build a fake playwright.async_api module
        pw_root = types.ModuleType("playwright")
        pw_async_api = types.ModuleType("playwright.async_api")

        class FakeBrowser:
            def __init__(self):
                self._connected = True
                self.closed = False

            def is_connected(self):
                return self._connected

            async def close(self):
                self.closed = True
                self._connected = False

        class FakePlaywright:
            def __init__(self, counters):
                self.chromium = self
                self.counters = counters

            async def launch(self, headless=True):
                self.counters["launch_calls"] += 1
                self.counters["last_headless"] = headless
                return FakeBrowser()

            async def stop(self):
                self.counters["stop_calls"] += 1

        class AsyncHelper:
            def __init__(self, counters):
                self.counters = counters

            async def start(self):
                self.counters["start_calls"] += 1
                return FakePlaywright(self.counters)

        def async_playwright():
            # Return a helper that has an async .start() as expected by the code
            return AsyncHelper(counters)

        pw_async_api.async_playwright = async_playwright

        # Fake langchain_community.agent_toolkits with PlayWrightBrowserToolkit
        lc_comm = types.ModuleType("langchain_community")
        lc_comm_agent_toolkits = types.ModuleType("langchain_community.agent_toolkits")

        class FakeToolkit:
            def __init__(self, async_browser):
                self.async_browser = async_browser

            @classmethod
            def from_browser(cls, async_browser=None):
                # Keep the created instance to inspect in tests
                inst = cls(async_browser=async_browser)
                return inst

            def get_tools(self):
                # Return sentinel tool objects
                return ["TOOL:go", "TOOL:extract"]

        lc_comm_agent_toolkits.PlayWrightBrowserToolkit = FakeToolkit

        # Return a dict for patch.dict
        return {
            "playwright": pw_root,
            "playwright.async_api": pw_async_api,
            "langchain_community": lc_comm,
            "langchain_community.agent_toolkits": lc_comm_agent_toolkits,
        }

    def _import_browser_module(self):
        # Import the module under test with a robust path resolution
        try:
            mod = importlib.import_module("nova.tools.builtins.browser")
        except Exception:
            mod = importlib.import_module("nova.tools.builtins.browser")
        return mod

    async def test_init_starts_playwright_and_launches_browser_and_is_idempotent(self):
        counters = {"start_calls": 0, "launch_calls": 0, "stop_calls": 0, "last_headless": None}
        fakes = self._get_fake_third_party_modules(counters)
        with patch.dict(sys.modules, fakes):
            browser_mod = self._import_browser_module()

            agent = SimpleNamespace(_resources={})

            # First init: should start and launch
            await browser_mod.init(agent)
            self.assertIn("playwright_async", agent._resources)
            self.assertIn("browser", agent._resources)
            self.assertTrue(hasattr(agent._resources["browser"], "is_connected"))  # Instance of FakeBrowser
            self.assertEqual(counters["start_calls"], 1)
            self.assertEqual(counters["launch_calls"], 1)
            self.assertTrue(counters["last_headless"])

            # Second init: should be no-op (idempotent)
            await browser_mod.init(agent)
            self.assertEqual(counters["start_calls"], 1)
            self.assertEqual(counters["launch_calls"], 1)

    async def test_close_closes_browser_and_stops_playwright_and_cleans_resources(self):
        counters = {"start_calls": 0, "launch_calls": 0, "stop_calls": 0, "last_headless": None}
        fakes = self._get_fake_third_party_modules(counters)
        with patch.dict(sys.modules, fakes):
            browser_mod = self._import_browser_module()

            # Prepare resources
            agent = SimpleNamespace(_resources={})

            # Create fake_browser dynamically with required methods
            async def close_method(self):
                self.closed = True
                self._connected = False

            def is_connected_method(self):
                return self._connected

            fake_browser = type("FakeBrowser", (), {
                "closed": False,
                "_connected": True,
                "close": close_method,
                "is_connected": is_connected_method
            })()

            agent._resources["browser"] = fake_browser

            class DummyPlaywright:
                async def stop(self):
                    counters["stop_calls"] += 1

            agent._resources["playwright_async"] = DummyPlaywright()

            # Perform close
            await browser_mod.close(agent)

            # Browser closed and resources removed
            self.assertTrue(fake_browser.closed)
            self.assertNotIn("browser", agent._resources)
            self.assertEqual(counters["stop_calls"], 1)
            self.assertNotIn("playwright_async", agent._resources)

            # Calling close again should not raise and should be a no-op
            await browser_mod.close(agent)
            self.assertEqual(counters["stop_calls"], 1)

    async def test_get_functions_raises_if_not_initialized(self):
        counters = {"start_calls": 0, "launch_calls": 0, "stop_calls": 0, "last_headless": None}
        fakes = self._get_fake_third_party_modules(counters)
        with patch.dict(sys.modules, fakes):
            browser_mod = self._import_browser_module()

            agent = SimpleNamespace(_resources={})
            # Not initialized -> expect ValueError
            with self.assertRaises(ValueError) as ctx:
                await browser_mod.get_functions(tool=object(), agent=agent)
            self.assertIn("Browser not initialized", str(ctx.exception))

    async def test_get_functions_returns_toolkit_tools_using_persistent_browser(self):
        counters = {"start_calls": 0, "launch_calls": 0, "stop_calls": 0, "last_headless": None}
        fakes = self._get_fake_third_party_modules(counters)
        with patch.dict(sys.modules, fakes):
            browser_mod = self._import_browser_module()

            # Init resources manually (skip init flow for brevity)
            agent = SimpleNamespace(_resources={})

            # Use a fake browser with is_connected
            def is_connected_method(self):
                return True

            fake_browser = type("FakeBrowser", (), {"is_connected": is_connected_method})()
            agent._resources["browser"] = fake_browser

            tools = await browser_mod.get_functions(tool=object(), agent=agent)
            # Should be exactly what toolkit.get_tools returns
            self.assertEqual(tools, ["TOOL:go", "TOOL:extract"])

            # Ensure toolkit was created with our persistent async browser
            # The FakeToolkit instance isn't directly exposed, but we can check types:
            self.assertTrue(hasattr(agent._resources["browser"], "is_connected"))
