import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

VIDEO_RE = re.compile(r"https://www\.tiktok\.com/@[A-Za-z0-9._-]+/video/\d+")


def normalize(url: str) -> str | None:
    if not url:
        return None
    m = VIDEO_RE.search(url)
    return m.group(0) if m else None


def discover(query: str, max_scrolls: int, max_results: int) -> tuple[list[dict], dict]:
    search_url = f"https://www.tiktok.com/search?q={quote(query)}"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            locale="ja-JP",
            viewport={"width": 1440, "height": 1800},
        )
        page = context.new_page()
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)

        blocked_text = ""
        try:
            blocked_text = page.locator("body").inner_text(timeout=5000)[:3000]
        except Exception:
            pass

        found = []
        seen = set()
        for _ in range(max_scrolls + 1):
            hrefs = page.locator("a[href*='/video/']").evaluate_all("els => els.map(e => e.href)")
            for href in hrefs:
                url = normalize(href)
                if url and url not in seen:
                    seen.add(url)
                    found.append({"url": url, "query": query, "source": "tiktok_browser_search"})
                    if len(found) >= max_results:
                        break
            if len(found) >= max_results:
                break
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(1800)

        diagnostics = {
            "search_url": search_url,
            "title": page.title(),
            "final_url": page.url,
            "body_sample": blocked_text,
        }
        browser.close()
        return found, diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True)
    parser.add_argument("--max-scrolls", type=int, default=6)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--output", default="tiktok_browser_discovery.json")
    args = parser.parse_args()

    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    results = []
    diagnostics = []
    errors = []
    seen = set()

    for query in queries:
        try:
            items, diag = discover(query, args.max_scrolls, args.max_results)
            diagnostics.append({"query": query, **diag, "count": len(items)})
            for item in items:
                if item["url"] not in seen:
                    seen.add(item["url"])
                    results.append(item)
        except Exception as exc:
            errors.append({"query": query, "error": str(exc)})

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "result_count": len(results),
        "results": results,
        "diagnostics": diagnostics,
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_count": len(results), "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
