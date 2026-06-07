"""
Bootstrap Cloudflare/Fandom browser cookies with Playwright.

The crawler still uses curl_cffi requests for normal traffic. This module only
opens Chromium once per process to collect challenge cookies such as
cf_clearance, then applies them to requests sessions.
"""

from __future__ import annotations

import logging
import os

from curl_cffi import requests

logger = logging.getLogger(__name__)

DEFAULT_COOKIE_URL = "https://duelmasters.fandom.com/wiki/DM-01_Base_Set"

_CACHE: dict[str, object] = {
    "cookies": None,
    "attempted": False,
    "playwright": None,
    "browser": None,
    "context": None,
}


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Invalid integer for %s=%r; using %s", name, value, default)
        return default


def get_cf_cookies(force: bool = False) -> dict[str, str]:
    """Return cached Fandom cookies, collecting them with Playwright once."""
    cached = _CACHE["cookies"]
    if isinstance(cached, dict) and not force:
        return cached
    if _CACHE["attempted"] and not force:
        return {}

    _CACHE["attempted"] = True
    cookie_url = os.getenv("CF_COOKIE_URL", DEFAULT_COOKIE_URL)
    headless = _env_bool("PLAYWRIGHT_HEADLESS", True)
    wait_until = os.getenv("PLAYWRIGHT_WAIT_UNTIL", "domcontentloaded")
    timeout_ms = _env_int("PLAYWRIGHT_TIMEOUT_MS", 120_000)
    settle_ms = _env_int("PLAYWRIGHT_SETTLE_MS", 5_000)

    try:
        from playwright.sync_api import sync_playwright

        logger.info("Collecting Fandom browser cookies with Playwright")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                )
                page = ctx.new_page()
                page.goto(cookie_url, wait_until=wait_until, timeout=timeout_ms)
                page.wait_for_timeout(settle_ms)
                cookies = ctx.cookies()
            finally:
                browser.close()

        collected = {c["name"]: c["value"] for c in cookies}
        _CACHE["cookies"] = collected
        logger.info(
            "Collected %s Fandom cookies (cf_clearance=%s)",
            len(collected),
            "yes" if "cf_clearance" in collected else "no",
        )
        return collected

    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.warning(
            "Could not collect Playwright cookies; continuing without them: %s",
            e,
        )
        _CACHE["cookies"] = {}
        return {}


def apply_cf_cookies(session: requests.Session, force: bool = False) -> requests.Session:
    """Attach cached Cloudflare/Fandom cookies to a curl_cffi requests session."""
    cookies = get_cf_cookies(force=force)
    if cookies:
        session.cookies.update(cookies)
    return session


def fetch_html_with_browser(url: str) -> str:
    """Fetch a page with Playwright as a fallback for curl/browser-cookie 403s."""
    wait_until = os.getenv("PLAYWRIGHT_WAIT_UNTIL", "domcontentloaded")
    timeout_ms = _env_int("PLAYWRIGHT_TIMEOUT_MS", 120_000)
    settle_ms = _env_int("PLAYWRIGHT_SETTLE_MS", 2_000)

    logger.info("Fetching with Playwright fallback: %s", url)
    ctx = _get_browser_context()
    page = ctx.new_page()
    try:
        page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        page.wait_for_timeout(settle_ms)
        return page.content()
    finally:
        page.close()


def _get_browser_context():
    """Reuse one browser context for repeated fallback fetches."""
    if _CACHE["context"] is not None:
        return _CACHE["context"]

    from playwright.sync_api import sync_playwright

    headless = _env_bool("PLAYWRIGHT_HEADLESS", True)
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=headless,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
    )
    _CACHE["playwright"] = playwright
    _CACHE["browser"] = browser
    _CACHE["context"] = context
    return context


def close_browser_context():
    """Close cached Playwright fallback resources."""
    context = _CACHE.get("context")
    browser = _CACHE.get("browser")
    playwright = _CACHE.get("playwright")

    if context is not None:
        context.close()
    if browser is not None:
        browser.close()
    if playwright is not None:
        playwright.stop()

    _CACHE["context"] = None
    _CACHE["browser"] = None
    _CACHE["playwright"] = None
