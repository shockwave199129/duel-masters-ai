"""
Duel Masters OCG Card Scraper
==============================
Scrapes all card data from the Duel Masters Fandom Wiki:
  1. Parses the OCG Sets list page to collect all set URLs
  2. Visits each set page to collect card links
  3. Visits each card page to scrape card details
  4. Saves results to JSON and CSV

Usage:
    pip install requests beautifulsoup4 lxml
    python dm_scraper.py

    # Limit sets for testing:
    python dm_scraper.py --max-sets 3

    # Resume from a checkpoint:
    python dm_scraper.py --resume

Output files:
    dm_cards.json         – all card data as JSON
    dm_cards.csv          – all card data as CSV
    dm_checkpoint.json    – progress checkpoint (for --resume)
    dm_errors.log         – pages that failed to scrape
"""

import argparse
import csv
import json
import logging
import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://duelmasters.fandom.com"
OCG_SETS_URL = f"{BASE_URL}/wiki/List_of_Duel_Masters_OCG_Sets"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xhtml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}

# Polite delays (seconds) – randomised to look more human
DELAY_MIN = 1.2
DELAY_MAX = 2.8

OUTPUT_JSON = Path("dm_cards.json")
OUTPUT_CSV  = Path("dm_cards.csv")
CHECKPOINT  = Path("dm_checkpoint.json")
ERROR_LOG   = Path("dm_errors.log")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("dm_scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url: str, retries: int = 3) -> BeautifulSoup | None:
    """Fetch a URL and return a BeautifulSoup object, or None on failure."""
    for attempt in range(1, retries + 1):
        try:
            resp = session.get(url, timeout=20)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.HTTPError as e:
            if resp.status_code == 429:
                wait = 30 * attempt
                log.warning(f"Rate-limited on {url}. Waiting {wait}s …")
                time.sleep(wait)
            else:
                log.error(f"HTTP {resp.status_code} for {url}: {e}")
                return None
        except requests.RequestException as e:
            wait = 5 * attempt
            log.warning(f"Attempt {attempt}/{retries} failed for {url}: {e}. Retrying in {wait}s …")
            time.sleep(wait)
    with open(ERROR_LOG, "a", encoding="utf-8") as f:
        f.write(url + "\n")
    log.error(f"Giving up on {url}")
    return None


def polite_sleep():
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


# ---------------------------------------------------------------------------
# Step 1 – Collect set URLs from the OCG Sets list page
# ---------------------------------------------------------------------------

def get_set_urls(max_sets: int | None = None) -> list[dict]:
    """
    Parse the OCG Sets list page and return a list of dicts:
        [{"name": "DM-01 Base Set", "url": "https://…"}, …]
    """
    log.info(f"Fetching set list from {OCG_SETS_URL}")
    soup = fetch(OCG_SETS_URL)
    if not soup:
        raise RuntimeError("Could not fetch the OCG Sets list page.")

    sets = []
    seen = set()
    content = soup.find("div", class_="mw-parser-output")
    if not content:
        content = soup

    for a in content.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(strip=True)
        # Filter: internal wiki links that look like set pages
        if not href.startswith("/wiki/"):
            continue
        # Skip non-set pages (galleries, categories, etc.)
        if any(skip in href for skip in [
            "Gallery", "Category:", "Special:", "Talk:", "File:",
            "List_of", "User:", "Template:", "#"
        ]):
            continue
        full_url = BASE_URL + href
        if full_url in seen:
            continue
        # Only include if the link text looks like a set code (DM-XX, DMRP-XX, etc.)
        if re.match(r"^(DM|DMR|DMRP|DMEX|DMD|DMC|DMS|DMSD|DMPCD|DMART|DM\d)", text):
            seen.add(full_url)
            sets.append({"name": text, "url": full_url})

    log.info(f"Found {len(sets)} sets.")
    if max_sets:
        sets = sets[:max_sets]
        log.info(f"Limiting to first {max_sets} sets.")
    return sets


# ---------------------------------------------------------------------------
# Step 2 – Collect card URLs from a set page
# ---------------------------------------------------------------------------

def get_card_urls_from_set(set_info: dict) -> list[dict]:
    """
    Visit a set page and return a list of card link dicts:
        [{"name": "…", "url": "…", "set": "…", "number": "…", "rarity": "…"}, …]
    """
    url = set_info["url"]
    set_name = set_info["name"]
    log.info(f"  Scraping set page: {set_name} → {url}")
    soup = fetch(url)
    if not soup:
        return []

    cards = []
    seen = set()

    # Most set pages have a wikitable listing cards with columns like:
    # Card Number | Name | Rarity | Card Type | Civilization | etc.
    tables = soup.find_all("table", class_=re.compile(r"wikitable"))
    for table in tables:
        rows = table.find_all("tr")
        # Detect header row
        headers = []
        header_row = table.find("tr")
        if header_row:
            headers = [th.get_text(strip=True).lower() for th in header_row.find_all(["th", "td"])]

        for row in rows[1:]:  # skip header
            cols = row.find_all(["td", "th"])
            if not cols:
                continue

            row_data = {
                "set": set_name,
                "set_url": url,
                "number": "",
                "name": "",
                "rarity": "",
                "card_url": "",
            }

            # Try to map columns by header names
            for i, col in enumerate(cols):
                if i >= len(headers):
                    break
                h = headers[i]
                val = col.get_text(strip=True)
                if "number" in h or "#" in h or "no" in h:
                    row_data["number"] = val
                elif "name" in h:
                    row_data["name"] = val
                    # Grab link if present
                    link = col.find("a", href=True)
                    if link and link["href"].startswith("/wiki/"):
                        row_data["card_url"] = BASE_URL + link["href"]
                elif "rarity" in h:
                    row_data["rarity"] = val

            # Fallback: if no headers matched, try to find any card link in the row
            if not row_data["card_url"]:
                for col in cols:
                    link = col.find("a", href=True)
                    if link and link["href"].startswith("/wiki/") and not any(
                        skip in link["href"] for skip in [
                            "Gallery", "Category:", "File:", "Special:", "List_of"
                        ]
                    ):
                        row_data["card_url"] = BASE_URL + link["href"]
                        if not row_data["name"]:
                            row_data["name"] = link.get_text(strip=True)
                        break

            if row_data["card_url"] and row_data["card_url"] not in seen:
                seen.add(row_data["card_url"])
                cards.append(row_data)

    # Fallback: scan all links on the page for card-like pages
    if not cards:
        content = soup.find("div", class_="mw-parser-output")
        if content:
            for a in content.find_all("a", href=True):
                href = a["href"]
                if not href.startswith("/wiki/"):
                    continue
                if any(skip in href for skip in [
                    "Gallery", "Category:", "Special:", "Talk:", "File:",
                    "List_of", "User:", "Template:", "#", set_info["name"].replace(" ", "_")
                ]):
                    continue
                full_url = BASE_URL + href
                if full_url not in seen:
                    seen.add(full_url)
                    cards.append({
                        "set": set_name,
                        "set_url": url,
                        "number": "",
                        "name": a.get_text(strip=True),
                        "rarity": "",
                        "card_url": full_url,
                    })

    log.info(f"    Found {len(cards)} card links in {set_name}.")
    return cards


# ---------------------------------------------------------------------------
# Step 3 – Scrape individual card page
# ---------------------------------------------------------------------------

def scrape_card(card_stub: dict) -> dict:
    """
    Visit a card page and return a dict of all available card data.
    Merges with the stub data collected from the set page.
    """
    url = card_stub["card_url"]
    soup = fetch(url)
    if not soup:
        return {**card_stub, "error": "fetch_failed"}

    data = {**card_stub}  # start with set-page data

    # --- Page title as card name (most reliable) ---
    title_el = soup.find("h1", class_="page-header__title") or soup.find("h1")
    if title_el:
        data["name"] = title_el.get_text(strip=True)

    # --- Infobox (portable or classic) ---
    # Fandom wikis use either .portable-infobox or table.infobox
    infobox = soup.find("aside", class_=re.compile(r"portable-infobox"))
    if not infobox:
        infobox = soup.find("table", class_=re.compile(r"infobox"))

    if infobox:
        # Portable infobox: <div data-source="…"><h3>…</h3><div>…</div></div>
        for item in infobox.find_all("div", attrs={"data-source": True}):
            key = item["data-source"].strip().lower().replace(" ", "_")
            value_el = item.find("div", class_=re.compile(r"pi-data-value"))
            if not value_el:
                value_el = item
            value = value_el.get_text(separator=" / ", strip=True)
            data[key] = value

        # Classic table infobox: rows of th/td
        for row in infobox.find_all("tr"):
            th = row.find("th")
            td = row.find("td")
            if th and td:
                key = th.get_text(strip=True).lower().replace(" ", "_")
                value = td.get_text(separator=" / ", strip=True)
                data[key] = value

    # --- Card text / effect box ---
    # Usually in a div with class containing "cardtext", "effect", or similar
    card_text_box = (
        soup.find("div", class_=re.compile(r"cardtext", re.I))
        or soup.find("div", class_=re.compile(r"effect", re.I))
        or soup.find("blockquote")
    )
    if card_text_box:
        data["card_text"] = card_text_box.get_text(separator="\n", strip=True)

    # --- Flavor text ---
    flavor = soup.find("i", class_=re.compile(r"flavor|italic", re.I))
    if flavor:
        data["flavor_text"] = flavor.get_text(strip=True)

    # --- Scrape main body paragraphs for any field-like lines ---
    # e.g. "Civilization: Fire" or "Power: 3000"
    content = soup.find("div", class_="mw-parser-output")
    if content:
        field_pattern = re.compile(
            r"^(Civilization|Power|Cost|Type|Race|Rarity|Set|Number|Flavor Text|Card Type|Shield Trigger|Evolution|Blocker|Artist|Illustrator)\s*[:：]\s*(.+)$",
            re.IGNORECASE,
        )
        for p in content.find_all(["p", "li"]):
            text = p.get_text(strip=True)
            m = field_pattern.match(text)
            if m:
                key = m.group(1).strip().lower().replace(" ", "_")
                val = m.group(2).strip()
                if key not in data:
                    data[key] = val

    # --- Card image URL ---
    img = None
    if infobox:
        img = infobox.find("img")
    if not img:
        img = soup.find("figure", class_=re.compile(r"pi-image"))
        if img:
            img = img.find("img")
    if img:
        data["image_url"] = img.get("src", img.get("data-src", ""))

    # --- Categories (useful for civilization, type, etc.) ---
    cats = []
    cat_section = soup.find("div", class_="page-header__categories") or \
                  soup.find("div", {"id": "catlinks"})
    if cat_section:
        for a in cat_section.find_all("a"):
            cats.append(a.get_text(strip=True))
    if cats:
        data["categories"] = " | ".join(cats)

    return data


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        with open(CHECKPOINT, encoding="utf-8") as f:
            return json.load(f)
    return {"completed_sets": [], "cards": []}


def save_checkpoint(state: dict):
    with open(CHECKPOINT, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def save_json(cards: list[dict]):
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cards, f, ensure_ascii=False, indent=2)
    log.info(f"Saved {len(cards)} cards to {OUTPUT_JSON}")


def save_csv(cards: list[dict]):
    if not cards:
        return
    # Collect all field names (preserve insertion order)
    fieldnames = list(dict.fromkeys(k for card in cards for k in card))
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cards)
    log.info(f"Saved {len(cards)} cards to {OUTPUT_CSV}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def run(max_sets: int | None = None, resume: bool = False):
    state = load_checkpoint() if resume else {"completed_sets": [], "cards": []}
    completed_sets = set(state["completed_sets"])
    all_cards: list[dict] = state["cards"]

    # Step 1: get set list
    sets = get_set_urls(max_sets)

    for i, set_info in enumerate(sets, 1):
        set_name = set_info["name"]
        if set_name in completed_sets:
            log.info(f"[{i}/{len(sets)}] Skipping {set_name} (already done).")
            continue

        log.info(f"[{i}/{len(sets)}] Processing set: {set_name}")

        # Step 2: get card stubs from set page
        polite_sleep()
        card_stubs = get_card_urls_from_set(set_info)

        # Step 3: scrape each card
        for j, stub in enumerate(card_stubs, 1):
            log.info(f"    [{j}/{len(card_stubs)}] Scraping card: {stub.get('name', '?')} — {stub['card_url']}")
            polite_sleep()
            card_data = scrape_card(stub)
            all_cards.append(card_data)

        # Checkpoint after each set
        state["completed_sets"].append(set_name)
        state["cards"] = all_cards
        save_checkpoint(state)

        # Persist outputs incrementally
        save_json(all_cards)
        save_csv(all_cards)

        log.info(f"  ✓ Set {set_name} done. Total cards so far: {len(all_cards)}")

    log.info(f"\n=== Scraping complete. {len(all_cards)} cards scraped. ===")
    save_json(all_cards)
    save_csv(all_cards)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Duel Masters OCG Fandom Wiki Scraper")
    parser.add_argument(
        "--max-sets", type=int, default=None,
        help="Limit the number of sets scraped (useful for testing)."
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from the last checkpoint (dm_checkpoint.json)."
    )
    args = parser.parse_args()

    run(max_sets=args.max_sets, resume=args.resume)
