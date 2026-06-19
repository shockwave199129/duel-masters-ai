"""
api_set_page.py — Level 2 (API): Discover card page links from a set wiki page.

Replaces set_page_crawler.crawl_set_page() when --use-api is enabled.
"""

from __future__ import annotations

import logging
from typing import Optional

from curl_cffi import requests

from scripts.api_client import (
    api_get,
    make_api_session,
    title_to_url,
    url_to_title,
)
from scripts.card_validation import is_card_wiki_title

logger = logging.getLogger(__name__)


def set_url_to_page_title(set_url: str) -> str:
    """Derive wiki page title from a set URL."""
    return url_to_title(set_url)


def _is_valid_card_title(title: str) -> bool:
    return is_card_wiki_title(title)


def get_set_card_links(
    session: requests.Session,
    set_page_title: str,
    set_code: str,
) -> list[dict]:
    """
    Fetch all card page links from a set wiki page via prop=links.

    Returns [{url, card_name, set_code}, ...].
    """
    seen_urls: set[str] = set()
    cards: list[dict] = []
    cont: dict[str, str] = {}

    while True:
        data = api_get(
            session,
            {
                "action": "query",
                "prop": "links",
                "titles": set_page_title,
                "plnamespace": "0",
                "pllimit": "500",
                **cont,
            },
        )
        pages = data.get("query", {}).get("pages", {})
        for page in pages.values():
            if page.get("missing"):
                logger.warning("Set page not found: %s", set_page_title)
                return []
            for link in page.get("links", []):
                title = link.get("title", "")
                if not _is_valid_card_title(title):
                    continue
                url = title_to_url(title)
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                cards.append(
                    {
                        "url": url,
                        "card_name": title,
                        "set_code": set_code,
                    }
                )
        if "continue" not in data:
            break
        cont = {"plcontinue": data["continue"]["plcontinue"]}

    logger.info("Set %s: API extracted %s card links", set_code, len(cards))
    return cards


def crawl_set_page_api(
    set_url: str,
    set_code: str,
    session: Optional[requests.Session] = None,
    set_page_title: Optional[str] = None,
) -> list[dict]:
    """
    API replacement for set_page_crawler.crawl_set_page().

    Args:
        set_url: Full wiki URL of the set page
        set_code: Set code tag for results
        session: Optional API session
        set_page_title: Wiki page title; derived from set_url if omitted
    """
    if session is None:
        session = make_api_session()
    title = set_page_title or set_url_to_page_title(set_url)
    return get_set_card_links(session, title, set_code)
