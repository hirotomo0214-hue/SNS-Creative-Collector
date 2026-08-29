import argparse
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

POST_RE = re.compile(r"^https://www\.instagram\.com/(?:p|reel)/[^/?#]+/?$")
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}


class SessionInvalid(RuntimeError):
    pass


def storage_states() -> list[Path]:
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    return [Path(p) for p in raw.split(":") if p and Path(p).is_file()]


def normalize_post_url(href: str) -> str | None:
    if not href:
        return None
    if href.startswith("/"):
        href = "https://www.instagram.com" + href
    href = href.split("?")[0].split("#")[0]
    if not href.endswith("/"):
        href += "/"
    return href if POST_RE.match(href) else None


def session_valid(page) -> bool:
    url = page.url.lower()
    if "/accounts/login" in url or "/challenge/" in url:
        return False
    try:
        if page.locator('input[name="username"]').count() > 0:
            return False
    except Exception:
        pass
    return True


def collect_links(page, search_url: str, max_scrolls: int = 8) -> list[str]:
    """Keep Instagram's discovery order instead of converting results to a set."""
    page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(5000)
    if not session_valid(page):
        raise SessionInvalid("Instagram session invalid or challenged")

    links: list[str] = []
    seen: set[str] = set()
    for _ in range(max_scrolls):
        hrefs = page.locator('a[href*="/p/"], a[href*="/reel/"]').evaluate_all(
            "els => els.map(e => e.href)"
        )
        for href in hrefs:
            url = normalize_post_url(href)
            if url and url not in seen:
                seen.add(url)
                links.append(url)
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1500)
    return links


def parse_public_metrics(description: str) -> tuple[int | None, int | None]:
    likes = None
    comments = None
    m = re.search(r"([\d,]+)\s+likes?,\s*([\d,]+)\s+comments?", description, re.IGNORECASE)
    if m:
        likes = int(m.group(1).replace(",", ""))
        comments = int(m.group(2).replace(",", ""))
    return likes, comments


def extract_account(description: str) -> str:
    patterns = [
        r"[\d,]+\s+likes?,\s*[\d,]+\s+comments?\s*-\s*([A-Za-z0-9._]+)\s+(?:on\s+Instagram\s+)?on\s+[A-Za-z]+\s+\d{1,2},\s+\d{4}",
        r"[\d,]+\s+likes?,\s*[\d,]+\s+comments?\s*-\s*([A-Za-z0-9._]+)\s*:",
        r"^[A-Za-z]+\s+\d{1,2},\s+\d{4}、([A-Za-z0-9._]+)\s*:",
        r"@([A-Za-z0-9._]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, description, re.IGNORECASE)
        if m:
            return m.group(1)
    return ""


def parse_iso_datetime(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_description_date(description: str) -> datetime | None:
    """Parse known Instagram og:description date formats.

    Instagram has returned both date-first metadata and English metadata such as
    "... username on August 27, 2026: ...". Japanese date text is also accepted.
    """
    text = " ".join((description or "").strip().split())
    if not text:
        return None

    english_patterns = [
        r"^([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})(?=[、,:\s]|$)",
        r"\bon\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})(?=[、,:\s]|$)",
    ]
    for pattern in english_patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if not m:
            continue
        month = MONTHS.get(m.group(1).lower())
        if not month:
            continue
        try:
            return datetime(int(m.group(3)), month, int(m.group(2)), tzinfo=timezone.utc)
        except ValueError:
            continue

    m = re.search(r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def extract_post_datetime(page, description: str) -> tuple[datetime | None, str | None]:
    description_dt = parse_description_date(description)
    if description_dt:
        return description_dt, "og_description"

    for selector, attribute in [
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="article:published_time"]', "content"),
        ('time[datetime]', "datetime"),
    ]:
        try:
            locator = page.locator(selector)
            count = min(locator.count(), 10)
            for i in range(count):
                dt = parse_iso_datetime(locator.nth(i).get_attribute(attribute))
                if dt:
                    return dt, selector
        except Exception:
            pass
    return None, None


def extract_post(page, url: str, cutoff: datetime, now: datetime) -> tuple[dict | None, str | None]:
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3500)
    if not session_valid(page):
        raise SessionInvalid("Instagram session invalid or challenged")

    description = ""
    try:
        meta = page.locator('meta[property="og:description"]').first
        if meta.count():
            description = meta.get_attribute("content") or ""
    except Exception:
        pass

    dt, date_source = extract_post_datetime(page, description)
    if dt is None:
        return None, "date_missing"
    if dt > now + timedelta(days=1):
        return None, "future_date"
    if dt < cutoff:
        return None, "outside_window"

    account = extract_account(description)
    likes, comments = parse_public_metrics(description)
    kind = "reel" if "/reel/" in url else "feed"
    return {
        "url": url,
        "type": kind,
        "posted_at": dt.isoformat(),
        "date_source": date_source,
        "account": account,
        "likes": likes,
        "comments": comments,
        "description": description[:1500],
    }, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keywords", required=True, help="Comma-separated keywords")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--max-posts", type=int, default=30)
    parser.add_argument("--output", default="instagram_research.json")
    args = parser.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    if not keywords:
        raise SystemExit("No keywords supplied")

    states = storage_states()
    if not states:
        raise SystemExit("No Instagram storageState files configured")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    candidate_urls: list[str] = []
    candidate_seen: set[str] = set()
    search_sources = []
    for keyword in keywords:
        tag = keyword.lstrip("#")
        search_sources.append((keyword, f"https://www.instagram.com/explore/tags/{quote(tag)}/"))
        search_sources.append((keyword, f"https://www.instagram.com/explore/search/keyword/?q={quote(keyword)}"))

    results = []
    errors = []
    skip_reasons = {"date_missing": 0, "outside_window": 0, "future_date": 0}
    date_source_counts: dict[str, int] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        active_idx = 0

        def make_context(index: int):
            return browser.new_context(
                storage_state=str(states[index]),
                locale="ja-JP",
                viewport={"width": 1440, "height": 1800},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )

        def search_with_active_session(keyword: str, search_url: str) -> list[str] | None:
            nonlocal active_idx
            idx = active_idx
            while idx < len(states):
                context = make_context(idx)
                page = context.new_page()
                try:
                    links = collect_links(page, search_url)
                    if idx != active_idx:
                        active_idx = idx
                        print(f"Instagram fallback activated: session {active_idx + 1}")
                    print(f"Search '{keyword}' via session {active_idx + 1}: {len(links)} links")
                    return links
                except SessionInvalid as exc:
                    errors.append({"stage": "search", "keyword": keyword, "session": idx + 1, "error": str(exc)})
                    print(f"Instagram session {idx + 1} invalid; trying next configured session")
                    idx += 1
                except Exception as exc:
                    errors.append({"stage": "search", "keyword": keyword, "session": idx + 1, "error": str(exc)})
                    print(f"Search error for '{keyword}' on session {idx + 1}: {exc}")
                    return None
                finally:
                    context.close()
            return None

        def post_with_active_session(url: str) -> tuple[bool, dict | None, str | None]:
            nonlocal active_idx
            idx = active_idx
            while idx < len(states):
                context = make_context(idx)
                page = context.new_page()
                try:
                    item, skip_reason = extract_post(page, url, cutoff, now)
                    if idx != active_idx:
                        active_idx = idx
                        print(f"Instagram fallback activated: session {active_idx + 1}")
                    return True, item, skip_reason
                except SessionInvalid as exc:
                    errors.append({"stage": "post", "url": url, "session": idx + 1, "error": str(exc)})
                    print(f"Instagram session {idx + 1} invalid; trying next configured session")
                    idx += 1
                except Exception as exc:
                    errors.append({"stage": "post", "url": url, "session": idx + 1, "error": str(exc)})
                    print(f"Post error on session {idx + 1}: {url}: {exc}")
                    return False, None, None
                finally:
                    context.close()
            return False, None, None

        for keyword, search_url in search_sources:
            links = search_with_active_session(keyword, search_url)
            if links is None:
                print(f"Search failed for '{keyword}'")
                continue
            for url in links:
                if url not in candidate_seen:
                    candidate_seen.add(url)
                    candidate_urls.append(url)

        for candidate_index, url in enumerate(candidate_urls, start=1):
            if len(results) >= args.max_posts:
                break
            print(f"Candidate {candidate_index}/{len(candidate_urls)}: {url}")
            worked, item, skip_reason = post_with_active_session(url)
            if not worked:
                continue
            if skip_reason:
                skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
                continue
            if item:
                source = item.get("date_source") or "unknown"
                date_source_counts[source] = date_source_counts.get(source, 0) + 1
                haystack = (item.get("description") or "").lower()
                item["matched_keywords"] = [k for k in keywords if k.lower() in haystack]
                results.append(item)

        browser.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "keywords": keywords,
        "candidate_count": len(candidate_urls),
        "recent_post_count": len(results),
        "skip_reasons": skip_reasons,
        "date_source_counts": date_source_counts,
        "posts": sorted(results, key=lambda x: x["posted_at"], reverse=True),
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Candidate URLs: {len(candidate_urls)}")
    print(f"Recent posts within {args.days} days: {len(results)}")
    print(f"Skip reasons: {skip_reasons}")
    print(f"Date sources for accepted posts: {date_source_counts}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
