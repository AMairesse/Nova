import sys
import types
import importlib
import asyncio
from unittest import TestCase


class BrowserBuiltinTests(TestCase):
    def _install_fakes_and_import(self):
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

        counters = {"start_calls": 0, "launch_calls": 0, "stop_calls": 0, "last_headless": None}

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

        # Inject fakes into sys.modules, and import the module under test fresh
        mapping = {
            "playwright": pw_root,
            "playwright.async_api": pw_async_api,
            "langchain_community": lc_comm,
            "langchain_community.agent_toolkits": lc_comm_agent_toolkits,
        }
        # Ensure we re-import the module under test using the fakes
        sys.modules.update(mapping)
        sys.modules.pop("nova.tools.builtins.browser", None)
        browser_mod = importlib.import_module("nova.tools.builtins.browser")
        return browser_mod, FakeBrowser, FakeToolkit, counters

    def test_init_starts_playwright_and_launches_browser_and_is_idempotent(self):
        browser_mod, FakeBrowser, FakeToolkit, counters = self._install_fakes_and_import()

        agent = type("A", (), {"_resources": {}})()

        # First init: should start and launch
        asyncio.run(browser_mod.init(agent))
        self.assertIn("playwright_async", agent._resources)
        self.assertIn("browser", agent._resources)
        self.assertIsInstance(agent._resources["browser"], FakeBrowser)
        self.assertEqual(counters["start_calls"], 1)
        self.assertEqual(counters["launch_calls"], 1)
        self.assertTrue(counters["last_headless"])

        # Second init: should be no-op (idempotent)
        asyncio.run(browser_mod.init(agent))
        self.assertEqual(counters["start_calls"], 1)
        self.assertEqual(counters["launch_calls"], 1)

    def test_close_closes_browser_and_stops_playwright_and_cleans_resources(self):
        browser_mod, FakeBrowser, FakeToolkit, counters = self._install_fakes_and_import()

        # Prepare resources
        agent = type("A", (), {"_resources": {}})()
        fake_browser = FakeBrowser()
        agent._resources["browser"] = fake_browser

        class DummyPlaywright:
            async def stop(self):
                counters["stop_calls"] += 1

        agent._resources["playwright_async"] = DummyPlaywright()

        # Perform close
        asyncio.run(browser_mod.close(agent))

        # Browser closed and resources removed
        self.assertTrue(fake_browser.closed)
        self.assertNotIn("browser", agent._resources)
        self.assertEqual(counters["stop_calls"], 1)
        self.assertNotIn("playwright_async", agent._resources)

        # Calling close again should not raise and should be a no-op
        asyncio.run(browser_mod.close(agent))
        self.assertEqual(counters["stop_calls"], 1)

    def test_get_functions_raises_if_not_initialized(self):
        browser_mod, FakeBrowser, FakeToolkit, counters = self._install_fakes_and_import()

        agent = type("A", (), {"_resources": {}})()
        # Not initialized -> expect ValueError
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(browser_mod.get_functions(tool=object(), agent=agent))
        self.assertIn("Browser not initialized", str(ctx.exception))

    def test_get_functions_returns_toolkit_tools_using_persistent_browser(self):
        browser_mod, FakeBrowser, FakeToolkit, counters = self._install_fakes_and_import()

        # Init resources manually (skip init flow for brevity)
        agent = type("A", (), {"_resources": {}})()
        fake_browser = FakeBrowser()
        agent._resources["browser"] = fake_browser

        tools = asyncio.run(browser_mod.get_functions(tool=object(), agent=agent))
        # Should be exactly what toolkit.get_tools returns
        self.assertEqual(tools, ["TOOL:go", "TOOL:extract"])

        # Ensure toolkit was created with our persistent async browser
        # The FakeToolkit instance isn't directly exposed, but we can check types:
        self.assertIsInstance(agent._resources["browser"], FakeBrowser)
