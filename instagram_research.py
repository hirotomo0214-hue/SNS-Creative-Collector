import argparse
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

POST_RE = re.compile(r"^https://www\.instagram\.com/(?:p|reel)/[^/?#]+/?$")


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


def collect_links(page, search_url: str, max_scrolls: int = 8) -> set[str]:
    page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(5000)
    if not session_valid(page):
        raise SessionInvalid("Instagram session invalid or challenged")

    links: set[str] = set()
    for _ in range(max_scrolls):
        hrefs = page.locator('a[href*="/p/"], a[href*="/reel/"]').evaluate_all(
            "els => els.map(e => e.href)"
        )
        for href in hrefs:
            url = normalize_post_url(href)
            if url:
                links.add(url)
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
    m = re.search(
        r"[\d,]+\s+likes?,\s*[\d,]+\s+comments?\s*-\s*([A-Za-z0-9._]+)\s*:",
        description,
        re.IGNORECASE,
    )
    if m:
        return m.group(1)
    m = re.search(r"@([A-Za-z0-9._]+)", description)
    return m.group(1) if m else ""


def extract_post(page, url: str, cutoff: datetime) -> dict | None:
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(3500)
    if not session_valid(page):
        raise SessionInvalid("Instagram session invalid or challenged")

    dt = None
    try:
        time_el = page.locator("time").first
        if time_el.count():
            raw = time_el.get_attribute("datetime")
            if raw:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        pass

    if dt is None or dt < cutoff:
        return None

    description = ""
    try:
        meta = page.locator('meta[property="og:description"]').first
        if meta.count():
            description = meta.get_attribute("content") or ""
    except Exception:
        pass

    account = extract_account(description)
    likes, comments = parse_public_metrics(description)
    kind = "reel" if "/reel/" in url else "feed"
    return {
        "url": url,
        "type": kind,
        "posted_at": dt.astimezone(timezone.utc).isoformat(),
        "account": account,
        "likes": likes,
        "comments": comments,
        "description": description[:1500],
    }


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

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    candidate_urls: set[str] = set()
    search_sources = []

    for keyword in keywords:
        tag = keyword.lstrip("#")
        search_sources.append((keyword, f"https://www.instagram.com/explore/tags/{quote(tag)}/"))
        search_sources.append((keyword, f"https://www.instagram.com/explore/search/keyword/?q={quote(keyword)}"))

    results = []
    errors = []

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

        def search_with_active_session(keyword: str, search_url: str) -> set[str] | None:
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

        def post_with_active_session(url: str) -> tuple[bool, dict | None]:
            nonlocal active_idx
            idx = active_idx
            while idx < len(states):
                context = make_context(idx)
                page = context.new_page()
                try:
                    item = extract_post(page, url, cutoff)
                    if idx != active_idx:
                        active_idx = idx
                        print(f"Instagram fallback activated: session {active_idx + 1}")
                    return True, item
                except SessionInvalid as exc:
                    errors.append({"stage": "post", "url": url, "session": idx + 1, "error": str(exc)})
                    print(f"Instagram session {idx + 1} invalid; trying next configured session")
                    idx += 1
                except Exception as exc:
                    errors.append({"stage": "post", "url": url, "session": idx + 1, "error": str(exc)})
                    print(f"Post error on session {idx + 1}: {url}: {exc}")
                    return False, None
                finally:
                    context.close()
            return False, None

        for keyword, search_url in search_sources:
            links = search_with_active_session(keyword, search_url)
            if links is not None:
                candidate_urls.update(links)
            else:
                print(f"Search failed for '{keyword}'")

        for candidate_index, url in enumerate(sorted(candidate_urls), start=1):
            if len(results) >= args.max_posts:
                break
            print(f"Candidate {candidate_index}/{len(candidate_urls)}: {url}")
            worked, item = post_with_active_session(url)
            if not worked:
                continue
            if item:
                haystack = (item.get("description") or "").lower()
                matched = [k for k in keywords if k.lower() in haystack]
                item["matched_keywords"] = matched
                results.append(item)

        browser.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "keywords": keywords,
        "candidate_count": len(candidate_urls),
        "recent_post_count": len(results),
        "posts": sorted(results, key=lambda x: x["posted_at"], reverse=True),
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Candidate URLs: {len(candidate_urls)}")
    print(f"Recent posts within {args.days} days: {len(results)}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
