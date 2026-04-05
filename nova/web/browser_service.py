from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright


class BrowserSessionError(Exception):
    pass


class BrowserSession:
    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._has_opened_page = False

    async def _ensure_page(self):
        if self._page is not None:
            return self._page

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox"],
        )
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()
        return self._page

    def _validate_http_url(self, url: str) -> str:
        candidate = str(url or "").strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"}:
            raise BrowserSessionError("Browser navigation only supports http and https URLs.")
        return candidate

    async def _require_open_page(self):
        if not self._has_opened_page or self._page is None:
            raise BrowserSessionError("No active page in the current browser session. Use `browse open` first.")
        return self._page

    async def open(self, url: str) -> dict[str, Any]:
        page = await self._ensure_page()
        target_url = self._validate_http_url(url)
        response = await page.goto(target_url)
        self._has_opened_page = True
        return {
            "url": str(page.url or target_url),
            "status": response.status if response is not None else None,
        }

    async def open_search_result(self, index: int, results: Sequence[dict[str, Any]]) -> dict[str, Any]:
        if index < 1 or index > len(results):
            raise BrowserSessionError(f"Search result {index} is out of range.")
        url = str(results[index - 1].get("url") or "").strip()
        if not url:
            raise BrowserSessionError(f"Search result {index} has no URL to open.")
        return await self.open(url)

    async def current(self) -> str:
        page = await self._require_open_page()
        return str(page.url or "")

    async def back(self) -> dict[str, Any]:
        page = await self._require_open_page()
        response = await page.go_back()
        if response is None:
            raise BrowserSessionError("Unable to navigate back; no previous page in the history.")
        return {
            "url": str(page.url or response.url),
            "status": response.status,
        }

    async def extract_text(self) -> str:
        page = await self._require_open_page()
        html_content = await page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        return " ".join(text for text in soup.stripped_strings)

    async def extract_links(self, *, absolute: bool = False) -> list[dict[str, str]]:
        page = await self._require_open_page()
        html_content = await page.content()
        soup = BeautifulSoup(html_content, "html.parser")
        links: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for anchor in soup.find_all("a"):
            href = str(anchor.get("href") or "").strip()
            if absolute and href:
                href = urljoin(page.url, href)
            text = " ".join(anchor.stripped_strings).strip()
            key = (href, text)
            if not href or key in seen:
                continue
            seen.add(key)
            links.append({"href": href, "text": text})
        return links

    async def get_elements(self, selector: str, attributes: Sequence[str]) -> list[dict[str, str]]:
        page = await self._require_open_page()
        elements = await page.query_selector_all(selector)
        results: list[dict[str, str]] = []
        for element in elements:
            result: dict[str, str] = {}
            for attribute in attributes:
                if attribute == "innerText":
                    value = await element.inner_text()
                else:
                    value = await element.get_attribute(attribute)
                if value is not None and str(value).strip():
                    result[attribute] = str(value)
            if result:
                results.append(result)
        return results

    async def click(self, selector: str) -> str:
        page = await self._require_open_page()
        selector_effective = f"{selector} >> visible=1"
        try:
            await page.click(selector_effective, strict=False, timeout=1_000)
        except PlaywrightTimeoutError as exc:
            raise BrowserSessionError(f"Unable to click on element '{selector}'.") from exc
        return f"Clicked element '{selector}'."

    async def close(self) -> None:
        page = self._page
        context = self._context
        browser = self._browser
        playwright = self._playwright

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._has_opened_page = False

        if page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                if browser.is_connected():
                    await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass
