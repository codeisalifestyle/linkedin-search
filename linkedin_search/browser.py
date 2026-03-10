"""Stealth browser/session helpers built on nodriver."""

from __future__ import annotations

import asyncio
import logging
from typing import Any


logger = logging.getLogger(__name__)


class LinkedInBrowser:
    """Thin wrapper around nodriver with auth/session helpers."""

    def __init__(self, *, headless: bool = False):
        self.headless = headless
        self.browser: Any = None
        self.tab: Any = None
        self._uc: Any = None
        self._cdp_network: Any = None
        self._cdp_storage: Any = None
        self._cdp_input: Any = None
        self._cdp_page: Any = None

    async def __aenter__(self) -> "LinkedInBrowser":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def start(self) -> None:
        """Start stealth browser without custom automation flags."""
        try:
            import nodriver as uc
            import nodriver.cdp.input_ as cdp_input
            import nodriver.cdp.network as cdp_network
            import nodriver.cdp.page as cdp_page
            import nodriver.cdp.storage as cdp_storage
        except ImportError as exc:
            raise RuntimeError("nodriver is required. Install dependencies first.") from exc

        self._uc = uc
        self._cdp_network = cdp_network
        self._cdp_storage = cdp_storage
        self._cdp_input = cdp_input
        self._cdp_page = cdp_page

        # Keep startup minimal so nodriver's own stealth profile remains intact.
        config_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "sandbox": True,
        }
        if self.headless:
            config_kwargs["browser_args"] = ["--window-size=1920,1080"]

        self.browser = await uc.start(**config_kwargs)
        self.tab = self.browser.main_tab
        await asyncio.sleep(1.5)

        await self._inject_stealth_script()
        if self.headless:
            await self._apply_headless_user_agent()

    async def close(self) -> None:
        if self.browser is None:
            return
        try:
            self.browser.stop()
        finally:
            self.browser = None
            self.tab = None

    async def _inject_stealth_script(self) -> None:
        script = """
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined,
              configurable: true,
            });
            window.chrome = window.chrome || { runtime: {} };
        """
        await self.tab.send(self._cdp_page.add_script_to_evaluate_on_new_document(source=script))

    async def _apply_headless_user_agent(self) -> None:
        """Replace HeadlessChrome token in headless mode."""
        try:
            current_ua = await self.tab.evaluate("navigator.userAgent")
            if not isinstance(current_ua, str) or "HeadlessChrome" not in current_ua:
                return
            clean_ua = current_ua.replace("HeadlessChrome", "Chrome")
            await self.tab.send(self._cdp_network.set_user_agent_override(user_agent=clean_ua))
            logger.info("Applied headless-safe user agent override.")
        except Exception as exc:
            logger.warning("Could not override headless user-agent: %s", exc)

    async def goto(self, url: str, *, wait_seconds: float = 0.0) -> None:
        await self.tab.get(url)
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

    async def evaluate(self, script: str) -> Any:
        return await self.tab.evaluate(script)

    async def select_first(self, selectors: list[str]) -> Any | None:
        for selector in selectors:
            try:
                element = await self.tab.select(selector)
                if element:
                    return element
            except Exception:
                continue
        return None

    async def select_all(self, selector: str) -> list[Any]:
        elements = await self.tab.select_all(selector)
        return elements or []

    async def press_key(self, key: str, code: str, virtual_key_code: int) -> None:
        await self.tab.send(
            self._cdp_input.dispatch_key_event(
                type_="keyDown",
                key=key,
                code=code,
                windows_virtual_key_code=virtual_key_code,
                native_virtual_key_code=virtual_key_code,
            )
        )
        await asyncio.sleep(0.05)
        await self.tab.send(
            self._cdp_input.dispatch_key_event(
                type_="keyUp",
                key=key,
                code=code,
                windows_virtual_key_code=virtual_key_code,
                native_virtual_key_code=virtual_key_code,
            )
        )

    async def set_cookies(self, cookies: list[dict[str, Any]]) -> None:
        if not self.tab:
            raise RuntimeError("Browser not started")

        await self.goto("about:blank", wait_seconds=0.5)
        for cookie in cookies:
            name = cookie.get("name")
            value = cookie.get("value", "")
            if not name:
                continue
            try:
                await self.tab.send(
                    self._cdp_network.set_cookie(
                        name=name,
                        value=value,
                        domain=cookie.get("domain", ".www.linkedin.com"),
                        path=cookie.get("path", "/"),
                        secure=bool(cookie.get("secure", False)),
                        http_only=bool(cookie.get("httpOnly", False)),
                    )
                )
            except Exception as exc:
                logger.debug("Skipping cookie '%s': %s", name, exc)

    async def get_cookies(self) -> list[dict[str, Any]]:
        response = await self.tab.send(self._cdp_storage.get_cookies())
        raw_cookies = response if isinstance(response, list) else getattr(response, "cookies", [])
        return [self._cookie_to_dict(cookie) for cookie in raw_cookies or []]

    @staticmethod
    def _cookie_to_dict(cookie: Any) -> dict[str, Any]:
        if isinstance(cookie, dict):
            return cookie
        result: dict[str, Any] = {}
        fields = [
            "name",
            "value",
            "domain",
            "path",
            "secure",
            "httpOnly",
            "expires",
            "sameSite",
        ]
        for field in fields:
            if hasattr(cookie, field):
                result[field] = getattr(cookie, field)
        return result

    async def detect_auth_state(self) -> str:
        """Detect whether session is authenticated."""
        current_url = str(self.tab.url)
        login_patterns = [
            "linkedin.com/login",
            "linkedin.com/uas/login",
            "linkedin.com/checkpoint/lg/",
            "linkedin.com/authwall",
        ]
        if any(pattern in current_url for pattern in login_patterns):
            return "login_page"

        page_text = str(await self.tab.evaluate("document.body ? document.body.innerText : ''"))
        if "too many requests" in page_text.lower() or "unusual activity" in page_text.lower():
            return "rate_limited"

        auth_signal = await self.tab.evaluate(
            """
            (() => {
              if (document.querySelector('nav.global-nav')) return true;
              if (document.querySelector('img.global-nav__me-photo')) return true;
              if (document.querySelector('input.search-global-typeahead__input')) return true;
              return false;
            })()
            """
        )
        if auth_signal:
            return "logged_in"

        if "linkedin.com" in current_url:
            return "error_page"
        return "unknown"

    async def ensure_authenticated(self) -> None:
        await self.goto("https://www.linkedin.com/feed", wait_seconds=2.5)
        state = await self.detect_auth_state()
        if state != "logged_in":
            raise RuntimeError(
                f"LinkedIn authentication failed (state={state}). "
                "Re-create your session cookies with `create-session`."
            )

