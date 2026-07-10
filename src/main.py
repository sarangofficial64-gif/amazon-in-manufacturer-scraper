import re
import time

import requests
from bs4 import BeautifulSoup

from apify import Actor

BASE = "https://www.amazon.in"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

# Bidi marks Amazon injects around labels/colons in detail bullets.
STRIP_CHARS = "‎‏: \t\n"

# "manufacturer" must be an EXACT label match -- Amazon has several other
# labels that contain the word "manufacturer" as a substring (e.g. "Is
# Discontinued By Manufacturer" [value "Yes"/"No"], "Manufacturer Part
# Number", "Manufacturer Contact Information") whose values are NOT the
# manufacturer name. A loose match here silently corrupts the field with
# garbage like manufacturer="No" -- confirmed happening in practice.
EXACT_LABELS = {
    "manufacturer": "manufacturer",
}

# These fields haven't shown the same false-positive risk, so substring
# matching is used to absorb Amazon's label wording drift ("Brand" vs "Brand
# Name", "Country of Origin" vs "Country of Publication", etc).
SUBSTRING_PATTERNS = [
    ("model number", "item_model_number"),
    ("country of", "country_of_origin"),
    ("importer", "importer"),
    ("packer", "packer"),
    ("brand", "brand"),
]


def match_label(label):
    norm = label.lower().strip()
    if norm in EXACT_LABELS:
        return EXACT_LABELS[norm]
    for pattern, key in SUBSTRING_PATTERNS:
        if pattern in norm:
            return key
    return None

BOUGHT_COUNT_RE = re.compile(r"([\d.]+)\s*([KM]?)\+?\s*bought", re.IGNORECASE)
BOUGHT_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "": 1}

BLOCK_MARKERS = [
    "Sorry, we just need to make sure you're not a robot",
    "api-services-support@amazon.com",
    "Enter the characters you see below",
]


def clean(text):
    return text.strip(STRIP_CHARS).strip()


def is_blocked(html):
    return any(m in html for m in BLOCK_MARKERS)


def parse_detail_bullets(soup, out):
    div = soup.select_one("#detailBulletsWrapper_feature_div")
    if not div:
        return
    for li in div.select("li"):
        bold = li.select_one(".a-text-bold")
        if not bold:
            continue
        label = clean(bold.get_text(" ", strip=True))
        key = match_label(label)
        if not key or key in out:
            continue
        value_span = bold.find_next_sibling("span")
        if value_span:
            out[key] = clean(value_span.get_text(" ", strip=True))


def parse_detail_table(soup, out):
    for table_id in ("#productDetails_detailBullets_sections1", "#productDetails_techSpec_section_1"):
        table = soup.select_one(table_id)
        if not table:
            continue
        for row in table.select("tr"):
            th, td = row.find("th"), row.find("td")
            if not th or not td:
                continue
            label = clean(th.get_text(" ", strip=True))
            key = match_label(label)
            if not key or key in out:
                continue
            out[key] = clean(td.get_text(" ", strip=True))


def parse_expander_tables(soup, out):
    # Newer "Product information" accordion (Item details / Materials & Care /
    # Measurements / ...) -- a third distinct layout Amazon uses on top of the
    # two above. Structural selector (class, not a specific container id)
    # since the id suffix varies per section.
    for table in soup.select("table.prodDetTable"):
        for row in table.select("tr"):
            th, td = row.find("th"), row.find("td")
            if not th or not td:
                continue
            label = clean(th.get_text(" ", strip=True))
            key = match_label(label)
            if not key or key in out:
                continue
            out[key] = clean(td.get_text(" ", strip=True))


def parse_byline_brand(soup, out):
    if "brand" in out:
        return
    byline = soup.select_one("#bylineInfo")
    if not byline:
        return
    text = clean(byline.get_text(" ", strip=True))
    m = re.match(r"(?:Visit the|Brand:)\s*(.+?)(?:\s+Store)?$", text, re.IGNORECASE)
    if m:
        out["brand"] = m.group(1).strip()


def parse_bought_count(text):
    m = BOUGHT_COUNT_RE.search(text or "")
    if not m:
        return None
    num, suffix = m.groups()
    try:
        val = float(num)
    except ValueError:
        return None
    return int(val * BOUGHT_MULTIPLIERS.get(suffix.upper(), 1))


def parse_social_proof(soup, out):
    # Absent on lower-selling ASINs -- that's expected, not an error. It also
    # flickers between requests for the same ASIN, which is why the caller
    # retries with a fresh proxy IP rather than trusting a single miss.
    el = soup.select_one("#social-proofing-faceout-title-tk_bought")
    if not el:
        return
    text = clean(el.get_text(" ", strip=True))
    if not text:
        return
    out["bought_past_month_raw"] = text
    count = parse_bought_count(text)
    if count is not None:
        out["bought_past_month_min"] = count


def parse_product_page(html):
    soup = BeautifulSoup(html, "html.parser")
    out = {}
    parse_detail_bullets(soup, out)
    parse_detail_table(soup, out)
    parse_expander_tables(soup, out)
    parse_byline_brand(soup, out)
    parse_social_proof(soup, out)
    return out


async def fetch_one(asin, proxy_configuration, max_retries):
    url = f"{BASE}/dp/{asin}/"
    last_error = None

    for attempt in range(max_retries + 1):
        proxy_url = await proxy_configuration.new_url() if proxy_configuration else None
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        try:
            resp = requests.get(url, headers=HEADERS, proxies=proxies, timeout=25)
        except requests.RequestException as e:
            last_error = f"request_error: {e}"
            time.sleep(1)
            continue

        if resp.status_code == 404:
            return {"asin": asin, "error": "not_found"}
        if resp.status_code != 200 or is_blocked(resp.text):
            last_error = f"status_{resp.status_code}" if resp.status_code != 200 else "blocked"
            continue

        item = {"asin": asin}
        item.update(parse_product_page(resp.text))
        return item

    return {"asin": asin, "error": last_error or "failed"}


async def main():
    async with Actor:
        actor_input = await Actor.get_input() or {}
        asins = actor_input.get("asins") or []
        max_retries = actor_input.get("maxRetries", 2)

        if not asins:
            Actor.log.warning("No ASINs provided in input -- nothing to do.")
            return

        proxy_configuration = await Actor.create_proxy_configuration(groups=["RESIDENTIAL"])

        for i, asin in enumerate(asins, 1):
            item = await fetch_one(asin, proxy_configuration, max_retries)
            await Actor.push_data(item)
            Actor.log.info(
                f"[{i}/{len(asins)}] {asin} -> manufacturer={item.get('manufacturer')!r} "
                f"brand={item.get('brand')!r} bought={item.get('bought_past_month_raw')!r} "
                f"error={item.get('error')!r}"
            )
