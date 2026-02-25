#!/usr/bin/env python3
"""WFP Workday job scraper → RSS feed generator.

Scrapes https://wd3.myworkdaysite.com recruiting pages for WFP,
filters for international-professional (P/D) levels plus internships/fellowships,
excludes consultants and local/national grades, and writes an RSS 2.0 feed.
"""

import hashlib
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.dom import minidom

import requests
from bs4 import BeautifulSoup

# ── Configuration ────────────────────────────────────────────────────────────

BASE_URL = "https://wd3.myworkdaysite.com/en-GB/recruiting/wfp/job_openings"
JSON_ENDPOINT = "https://wd3.myworkdaysite.com/wday/cxs/wfp/job_openings/jobs"
FEED_FILE = Path(__file__).resolve().parent / "wfp_jobs.xml"
MAX_INCLUDED = 50
PAGE_SIZE = 20

HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Origin": "https://wd3.myworkdaysite.com",
    "Referer": BASE_URL,
}

FEED_TITLE = "WFP Job Vacancies"
FEED_LINK = BASE_URL
FEED_DESC = "List of vacancies at WFP"
SELF_URL = "https://cinfoposte.github.io/WFP-jobs/wfp_jobs.xml"

ATOM_NS = "http://www.w3.org/2005/Atom"
DC_NS = "http://purl.org/dc/elements/1.1/"

# ── Text normalisation helpers ───────────────────────────────────────────────

_DASH_RE = re.compile(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]")


def normalise(text: str) -> str:
    """Upper-case, normalise unicode dashes to ASCII hyphen, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text)
    text = _DASH_RE.sub("-", text)
    text = text.upper()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def expand_compact_grades(text: str) -> str:
    """Insert hyphen in compact grade forms: P4→P-4, D1→D-1, G6→G-6, SB1→SB-1, LSC10→LSC-10, NOA→NO-A."""
    text = re.sub(r"\b(P)\s*(\d)\b", r"\1-\2", text)
    text = re.sub(r"\b(D)\s*(\d)\b", r"\1-\2", text)
    text = re.sub(r"\b(G)\s*(\d)\b", r"\1-\2", text)
    text = re.sub(r"\b(SB)\s*(\d)\b", r"\1-\2", text)
    text = re.sub(r"\b(LSC)\s*(\d+)\b", r"\1-\2", text)
    text = re.sub(r"\b(NO)\s*([A-D])\b", r"\1-\2", text)
    return text

# ── Grade / level detection ──────────────────────────────────────────────────

_P_LEVELS = re.compile(r"\bP-[1-5]\b")
_D_LEVELS = re.compile(r"\bD-[12]\b")
_INTERN = re.compile(r"\b(INTERNSHIP|FELLOWSHIP)\b")
_CONSULTANT = re.compile(r"\bCONSULTAN")
_G_LEVELS = re.compile(r"\bG-[1-7]\b")
_NO_LEVELS = re.compile(r"\bNO-[A-D]\b")
_SB_LEVELS = re.compile(r"\bSB-[1-4]\b")
_LSC_LEVELS = re.compile(r"\bLSC-\d{1,2}\b")


def classify_job(text: str) -> tuple[str, list[str]]:
    """Return (decision, detected_grades) for normalised+expanded text.

    decision is one of 'include', 'exclude'.
    """
    n = expand_compact_grades(normalise(text))
    detected: list[str] = []

    # 1) Consultant → exclude
    if _CONSULTANT.search(n):
        return "exclude", ["CONSULTANT"]

    # 2) Excluded grades
    for pat, label in [
        (_G_LEVELS, "G-level"),
        (_NO_LEVELS, "NO-level"),
        (_SB_LEVELS, "SB-level"),
        (_LSC_LEVELS, "LSC-level"),
    ]:
        found = pat.findall(n)
        if found:
            return "exclude", found

    # 3) P/D levels → include
    pd = _P_LEVELS.findall(n) + _D_LEVELS.findall(n)
    if pd:
        return "include", pd

    # 4) Internship / fellowship → include
    intern = _INTERN.findall(n)
    if intern:
        return "include", intern

    # 5) Default → exclude
    return "exclude", []

# ── XML helpers ──────────────────────────────────────────────────────────────

_XML_ILLEGAL = re.compile(
    r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x84\x86-\x9F"
    r"\uD800-\uDFFF\uFDD0-\uFDEF\uFFFE\uFFFF]"
)


def xml_safe(text: str) -> str:
    """Remove XML 1.0 illegal characters."""
    return _XML_ILLEGAL.sub("", text)


def generate_numeric_id(url: str) -> str:
    """16-digit zero-padded numeric GUID derived from MD5 of URL."""
    hex_dig = hashlib.md5(url.encode()).hexdigest()
    return str(int(hex_dig[:16], 16) % 10000000000000000).zfill(16)

# ── Existing feed parsing ────────────────────────────────────────────────────


def load_existing_links() -> set[str]:
    """Return set of <link> values already in the feed file."""
    if not FEED_FILE.exists():
        return set()
    try:
        tree = ET.parse(FEED_FILE)
        return {el.text.strip() for el in tree.iter("link") if el.text}
    except ET.ParseError:
        return set()


def load_existing_items() -> list[dict]:
    """Parse existing feed and return list of item dicts."""
    items: list[dict] = []
    if not FEED_FILE.exists():
        return items
    try:
        tree = ET.parse(FEED_FILE)
    except ET.ParseError:
        return items
    for item_el in tree.iter("item"):
        d: dict[str, str] = {}
        for child in item_el:
            tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            if tag == "source":
                d["source_text"] = child.text or ""
                d["source_url"] = child.get("url", "")
            elif tag == "guid":
                d["guid"] = child.text or ""
                d["guid_perma"] = child.get("isPermaLink", "false")
            else:
                d[tag] = child.text or ""
        items.append(d)
    return items

# ── Workday endpoint discovery ───────────────────────────────────────────────


def discover_endpoint(session: requests.Session) -> str:
    """Return the Workday JSON jobs endpoint URL."""
    # Try the known endpoint first
    try:
        resp = session.post(
            JSON_ENDPOINT,
            json={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}},
            headers=HEADERS,
            timeout=30,
        )
        if resp.status_code == 200 and "jobPostings" in resp.text:
            print(f"[+] Endpoint confirmed: {JSON_ENDPOINT}")
            return JSON_ENDPOINT
    except requests.RequestException:
        pass

    # Fallback: scrape HTML for endpoint path
    for locale in ("en-GB", "en-US"):
        page_url = f"https://wd3.myworkdaysite.com/{locale}/recruiting/wfp/job_openings"
        try:
            resp = session.get(page_url, headers={
                "User-Agent": HEADERS["User-Agent"],
            }, timeout=30)
            if resp.status_code != 200:
                continue
            match = re.search(r'(/wday/cxs/[^"\']+/jobs)', resp.text)
            if match:
                endpoint = "https://wd3.myworkdaysite.com" + match.group(1)
                print(f"[+] Discovered endpoint from HTML ({locale}): {endpoint}")
                return endpoint
        except requests.RequestException:
            continue

    # Last resort: return the known endpoint anyway
    print("[!] Could not confirm endpoint; using default")
    return JSON_ENDPOINT

# ── Scraping ─────────────────────────────────────────────────────────────────


def fetch_listings(session: requests.Session, endpoint: str) -> list[dict]:
    """Paginate through Workday listings and return raw job dicts."""
    all_jobs: list[dict] = []
    offset = 0
    max_offset = 500  # safety cap

    while offset < max_offset:
        payload = {
            "limit": PAGE_SIZE,
            "offset": offset,
            "searchText": "",
            "appliedFacets": {},
        }
        try:
            resp = session.post(endpoint, json=payload, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            print(f"[!] Listing request failed at offset {offset}: {exc}")
            break

        postings = data.get("jobPostings", [])
        if not postings:
            break

        all_jobs.extend(postings)
        total = data.get("total", 0)
        offset += PAGE_SIZE

        if offset >= total:
            break

        time.sleep(0.5)  # polite delay

    print(f"[+] Fetched {len(all_jobs)} raw listings")
    return all_jobs


def build_job_url(posting: dict) -> str:
    """Build the public job detail URL from a posting dict."""
    ext_path = posting.get("externalPath", "")
    if ext_path:
        return f"https://wd3.myworkdaysite.com/en-GB/recruiting/wfp/job_openings{ext_path}"
    # Fallback: use bulletFields or title slug
    return BASE_URL


def extract_title(posting: dict) -> str:
    """Extract job title from posting dict."""
    return xml_safe(posting.get("title", "Unknown Position").strip())


def extract_location(posting: dict) -> str:
    """Extract location from posting dict."""
    loc = posting.get("locationsText", "")
    if not loc:
        bullets = posting.get("bulletFields", [])
        if bullets:
            loc = bullets[0]
    return xml_safe(loc.strip())


def extract_posted_date(posting: dict) -> str:
    """Extract posted date string from posting dict."""
    posted = posting.get("postedOn", "")
    if not posted:
        posted = posting.get("startDate", "")
    return posted.strip()


def fetch_detail_text(session: requests.Session, url: str) -> str:
    """Fetch job detail page and return visible text for grade detection."""
    # Try JSON detail endpoint first
    # URL pattern: .../job_openings/job/WFP-XXXXXX
    # JSON detail: .../wday/cxs/wfp/job_openings/WFP-XXXXXX
    match = re.search(r"/job_openings(/job/.+)$", url)
    if match:
        json_path = match.group(1).replace("/job/", "/")
        detail_endpoint = f"https://wd3.myworkdaysite.com/wday/cxs/wfp/job_openings{json_path}"
        try:
            resp = session.get(detail_endpoint, headers=HEADERS, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                # Extract text from jobPostingInfo
                info = data.get("jobPostingInfo", {})
                parts = [
                    info.get("title", ""),
                    info.get("location", ""),
                    info.get("jobDescription", ""),
                    info.get("additionalInformation", ""),
                ]
                # Also check for any sub-fields
                for key in ("jobRequisitionId", "externalJobTitle", "jobFamilyGroup",
                            "managementLevel", "jobCategory", "timeType"):
                    val = info.get(key, "")
                    if val:
                        parts.append(str(val))

                combined = " ".join(parts)
                if combined.strip():
                    # Parse HTML out of description fields
                    soup = BeautifulSoup(combined, "html.parser")
                    return soup.get_text(separator=" ", strip=True)
        except (requests.RequestException, ValueError):
            pass

    # Fallback: fetch the HTML page
    try:
        resp = session.get(url, headers={
            "User-Agent": HEADERS["User-Agent"],
            "Referer": BASE_URL,
        }, timeout=30)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            return soup.get_text(separator=" ", strip=True)
    except requests.RequestException:
        pass

    return ""

# ── RSS generation ───────────────────────────────────────────────────────────


def build_rss(items: list[dict]) -> str:
    """Build RSS 2.0 XML string from a list of item dicts."""
    now_rfc2822 = format_datetime(datetime.now(timezone.utc))

    # Register namespaces so they serialise cleanly.
    # Do NOT also set xmlns:* manually — register_namespace handles it
    # and duplicates cause an expat parse error in minidom.
    ET.register_namespace("atom", ATOM_NS)
    ET.register_namespace("dc", DC_NS)

    rss = ET.Element("rss", version="2.0")
    # dc namespace won't auto-emit (no dc: elements used), so set it manually.
    # atom namespace is auto-emitted via the atom:link element below.
    rss.set("xmlns:dc", DC_NS)

    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = FEED_TITLE
    ET.SubElement(channel, "link").text = FEED_LINK
    ET.SubElement(channel, "description").text = FEED_DESC
    ET.SubElement(channel, "language").text = "en"

    # atom:link self
    atom_link = ET.SubElement(channel, ET.QName(ATOM_NS, "link"))
    atom_link.set("href", SELF_URL)
    atom_link.set("rel", "self")
    atom_link.set("type", "application/rss+xml")

    ET.SubElement(channel, "pubDate").text = now_rfc2822

    for item_data in items:
        item = ET.SubElement(channel, "item")
        ET.SubElement(item, "title").text = xml_safe(item_data.get("title", ""))
        ET.SubElement(item, "link").text = item_data.get("link", "")
        # description placeholder — will be replaced with CDATA via minidom
        desc_el = ET.SubElement(item, "description")
        desc_el.text = item_data.get("description", "")

        guid_el = ET.SubElement(item, "guid")
        guid_el.set("isPermaLink", "false")
        guid_el.text = item_data.get("guid", "")

        ET.SubElement(item, "pubDate").text = item_data.get("pubDate", now_rfc2822)

        source_el = ET.SubElement(item, "source")
        source_el.set("url", FEED_LINK)
        source_el.text = FEED_TITLE

    # Serialise via minidom to inject CDATA sections
    rough = ET.tostring(rss, encoding="unicode", xml_declaration=False)
    dom = minidom.parseString(f'<?xml version="1.0" encoding="UTF-8"?>{rough}')

    # Replace description text nodes with CDATA
    for desc_node in dom.getElementsByTagName("description"):
        if desc_node.parentNode.tagName == "item":
            text = ""
            for child in list(desc_node.childNodes):
                text += child.toxml()
                desc_node.removeChild(child)
            cdata = dom.createCDATASection(text)
            desc_node.appendChild(cdata)

    xml_str = dom.toprettyxml(indent="  ", encoding="UTF-8").decode("UTF-8")
    # minidom adds an extra xml declaration; normalise
    # Remove blank lines that minidom sometimes inserts
    lines = [line for line in xml_str.split("\n") if line.strip()]
    return "\n".join(lines) + "\n"

# ── Main logic ───────────────────────────────────────────────────────────────


def main() -> None:
    print("=" * 60)
    print("WFP Workday Job Scraper → RSS Feed")
    print("=" * 60)

    session = requests.Session()

    # 1) Discover endpoint
    endpoint = discover_endpoint(session)

    # 2) Fetch all listings
    raw_listings = fetch_listings(session, endpoint)

    # 3) Load existing items
    existing_links = load_existing_links()
    existing_items = load_existing_items()
    print(f"[+] Existing feed has {len(existing_items)} items, {len(existing_links)} links")

    # 4) Filter and collect new jobs
    now_rfc2822 = format_datetime(datetime.now(timezone.utc))
    new_items: list[dict] = []
    included = 0
    skipped_dup = 0
    skipped_filter = 0
    detail_fetched = 0

    for posting in raw_listings:
        if included >= MAX_INCLUDED:
            break

        title = extract_title(posting)
        job_url = build_job_url(posting)
        location = extract_location(posting)

        # Skip if already in feed
        if job_url in existing_links:
            skipped_dup += 1
            continue

        # Quick classify from title + location
        combined_text = f"{title} {location}"
        decision, grades = classify_job(combined_text)

        # If not conclusive from title, fetch detail page
        if decision == "exclude" and not grades:
            detail_text = fetch_detail_text(session, job_url)
            detail_fetched += 1
            if detail_text:
                combined_text = f"{title} {location} {detail_text}"
                decision, grades = classify_job(combined_text)
            time.sleep(0.3)  # polite delay

        if decision != "include":
            skipped_filter += 1
            continue

        # Parse posted date
        posted_str = extract_posted_date(posting)
        if posted_str:
            try:
                dt = datetime.fromisoformat(posted_str.replace("Z", "+00:00"))
                pub_date = format_datetime(dt)
            except (ValueError, TypeError):
                pub_date = now_rfc2822
        else:
            pub_date = now_rfc2822

        grade_str = ", ".join(grades) if grades else ""
        desc_parts = [
            f"WFP has a vacancy for the position of {xml_safe(title)}.",
            f"Location: {xml_safe(location)}." if location else "",
        ]
        if grade_str:
            desc_parts.append(f"Level: {xml_safe(grade_str)}.")
        description = " ".join(p for p in desc_parts if p)

        new_items.append({
            "title": title,
            "link": job_url,
            "description": description,
            "guid": generate_numeric_id(job_url),
            "pubDate": pub_date,
        })
        existing_links.add(job_url)
        included += 1

    print(f"\n[+] Results:")
    print(f"    New included:   {len(new_items)}")
    print(f"    Skipped (dup):  {skipped_dup}")
    print(f"    Skipped (filter): {skipped_filter}")
    print(f"    Detail pages fetched: {detail_fetched}")

    # 5) Merge old + new
    all_items = existing_items + [
        {
            "title": it["title"],
            "link": it["link"],
            "description": it["description"],
            "guid": it["guid"],
            "pubDate": it["pubDate"],
        }
        for it in new_items
    ]

    # 6) Write RSS (even if empty — produces a valid scaffold)
    if not new_items and not existing_items:
        print("\n[+] No items to write; keeping existing feed unchanged")
        return

    rss_xml = build_rss(all_items)
    FEED_FILE.write_text(rss_xml, encoding="utf-8")
    print(f"\n[+] Wrote {FEED_FILE} ({len(all_items)} total items)")


if __name__ == "__main__":
    main()
