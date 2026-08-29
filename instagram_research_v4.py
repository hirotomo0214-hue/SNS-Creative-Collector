import argparse
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

POST_RE = re.compile(r"^https://www\.instagram\.com/(?:p|reel)/[^/?#]+/?$")
JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
MONTHS = {m.lower(): i for i, m in enumerate([
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]) if m}


class SessionInvalid(RuntimeError):
    pass


def parse_csv(raw):
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def storage_states():
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    return [Path(p) for p in raw.split(":") if p and Path(p).is_file()]


def normalize_post_url(href):
    if not href:
        return None
    if href.startswith("/"):
        href = "https://www.instagram.com" + href
    href = href.split("?")[0].split("#")[0]
    if not href.endswith("/"):
        href += "/"
    return href if POST_RE.match(href) else None


def session_valid(page):
    u = page.url.lower()
    if "/accounts/login" in u or "/challenge/" in u:
        return False
    try:
        return page.locator('input[name="username"]').count() == 0
    except Exception:
        return True


def collect_links(page, search_url, max_scrolls=4):
    page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(4000)
    if not session_valid(page):
        raise SessionInvalid("Instagram session invalid or challenged")
    out, seen = [], set()
    for _ in range(max_scrolls):
        hrefs = page.locator('a[href*="/p/"], a[href*="/reel/"]').evaluate_all("els => els.map(e => e.href)")
        for href in hrefs:
            url = normalize_post_url(href)
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        page.mouse.wheel(0, 1800)
        page.wait_for_timeout(1200)
    return out


def parse_iso(raw):
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_description_date(text):
    text = " ".join((text or "").split())
    for pattern in [
        r"^([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})",
        r"\bon\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})",
    ]:
        m = re.search(pattern, text, re.I)
        if m and m.group(1).lower() in MONTHS:
            try:
                return datetime(int(m.group(3)), MONTHS[m.group(1).lower()], int(m.group(2)), tzinfo=timezone.utc)
            except ValueError:
                pass
    m = re.search(r"(?<!\d)(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def extract_date(page, description):
    dt = parse_description_date(description)
    if dt:
        return dt, "og_description", {}
    diagnostics = {}
    selectors = [
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="article:published_time"]', "content"),
        ('time[datetime]', "datetime"),
    ]
    for selector, attr in selectors:
        vals = []
        try:
            loc = page.locator(selector)
            for i in range(min(loc.count(), 10)):
                raw = loc.nth(i).get_attribute(attr)
                if raw:
                    vals.append(raw)
                parsed = parse_iso(raw)
                if parsed:
                    diagnostics[selector] = vals[:5]
                    return parsed, selector, diagnostics
        except Exception as exc:
            vals.append(f"error:{type(exc).__name__}")
        diagnostics[selector] = vals[:5]
    return None, None, diagnostics


def metrics(description):
    m = re.search(r"([\d,]+)\s+likes?,\s*([\d,]+)\s+comments?", description or "", re.I)
    if not m:
        return None, None
    return int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))


def account_from(description):
    for pattern in [
        r"[\d,]+\s+likes?,\s*[\d,]+\s+comments?\s*-\s*([A-Za-z0-9._]+)\s*:",
        r"@([A-Za-z0-9._]+)",
    ]:
        m = re.search(pattern, description or "", re.I)
        if m:
            return m.group(1)
    return ""


def extract_post(page, url, cutoff, now):
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3000)
    if not session_valid(page):
        raise SessionInvalid("Instagram session invalid or challenged")

    description = ""
    try:
        meta = page.locator('meta[property="og:description"]').first
        if meta.count():
            description = meta.get_attribute("content") or ""
    except Exception:
        pass

    dt, source, diagnostics = extract_date(page, description)
    if not dt:
        return None, "date_missing", {
            "url": url,
            "page_url": page.url,
            "title": page.title()[:200],
            "description_preview": description[:300],
            "date_diagnostics": diagnostics,
        }
    if dt > now + timedelta(days=1):
        return None, "future_date", None
    if dt < cutoff:
        return None, "outside_window", None

    likes, comments = metrics(description)
    return {
        "url": url,
        "type": "reel" if "/reel/" in url else "feed",
        "posted_at": dt.isoformat(),
        "date_source": source,
        "account": account_from(description),
        "likes": likes,
        "comments": comments,
        "description": description[:1500],
    }, None, None


def matched_terms(text, terms):
    lower = (text or "").lower()
    return [t for t in terms if t.lower() in lower]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keywords", required=True)
    ap.add_argument("--anchor-keywords", default="")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--max-posts", type=int, default=30)
    ap.add_argument("--max-candidates", type=int, default=30)
    ap.add_argument("--require-japanese", action="store_true")
    ap.add_argument("--output", default="instagram_research.json")
    args = ap.parse_args()

    keywords, anchors = parse_csv(args.keywords), parse_csv(args.anchor_keywords)
    states = storage_states()
    if not keywords:
        raise SystemExit("No keywords supplied")
    if not states:
        raise SystemExit("No Instagram storageState files configured")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    candidate_urls, seen, sources = [], set(), {}
    results, errors, date_debug = [], [], []
    skip = {"date_missing": 0, "outside_window": 0, "future_date": 0, "irrelevant_recent": 0, "non_japanese": 0}
    recent_count = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        def make_context(index):
            return browser.new_context(storage_state=str(states[index]), locale="ja-JP", viewport={"width": 1440, "height": 1800})

        for keyword in keywords:
            for search_url in [
                f"https://www.instagram.com/explore/tags/{quote(keyword.lstrip('#'))}/",
                f"https://www.instagram.com/explore/search/keyword/?q={quote(keyword)}",
            ]:
                context = make_context(0)
                page = context.new_page()
                try:
                    links = collect_links(page, search_url)
                    print(f"Search '{keyword}': {len(links)} links")
                    for url in links:
                        sources.setdefault(url, [])
                        if keyword not in sources[url]:
                            sources[url].append(keyword)
                        if url not in seen:
                            seen.add(url)
                            candidate_urls.append(url)
                except Exception as exc:
                    errors.append({"stage": "search", "keyword": keyword, "error": str(exc)})
                finally:
                    context.close()

        to_check = candidate_urls[: max(0, args.max_candidates)]
        for i, url in enumerate(to_check, 1):
            if len(results) >= args.max_posts:
                break
            print(f"Candidate {i}/{len(to_check)} (discovered {len(candidate_urls)} total): {url}")
            context = make_context(0)
            page = context.new_page()
            try:
                item, reason, debug = extract_post(page, url, cutoff, now)
            except Exception as exc:
                errors.append({"stage": "post", "url": url, "error": str(exc)})
                context.close()
                continue
            context.close()

            if reason:
                skip[reason] = skip.get(reason, 0) + 1
                if debug and len(date_debug) < 10:
                    date_debug.append(debug)
                continue
            recent_count += 1
            desc = item.get("description") or ""
            item["matched_keywords"] = matched_terms(desc, keywords)
            item["matched_anchor_keywords"] = matched_terms(desc, anchors)
            item["discovered_by_keywords"] = sources.get(url, [])
            item["japanese_text"] = bool(JAPANESE_RE.search(desc))
            if anchors and not item["matched_anchor_keywords"]:
                skip["irrelevant_recent"] += 1
                continue
            if not anchors and not item["matched_keywords"]:
                skip["irrelevant_recent"] += 1
                continue
            if args.require_japanese and not item["japanese_text"]:
                skip["non_japanese"] += 1
                continue
            results.append(item)
        browser.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "keywords": keywords,
        "anchor_keywords": anchors,
        "candidate_count": len(candidate_urls),
        "candidate_check_limit": args.max_candidates,
        "checked_candidate_count": min(len(candidate_urls), args.max_candidates),
        "recent_candidate_count": recent_count,
        "accepted_post_count": len(results),
        "skip_reasons": skip,
        "posts": sorted(results, key=lambda x: x["posted_at"], reverse=True),
        "date_debug": date_debug,
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Discovered candidate URLs: {len(candidate_urls)}")
    print(f"Checked candidate URLs: {payload['checked_candidate_count']}")
    print(f"Accepted relevant posts: {len(results)}")
    print(f"Skip reasons: {skip}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
