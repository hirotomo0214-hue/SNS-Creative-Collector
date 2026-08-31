import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

VIDEO_RE = re.compile(r"https://www\.tiktok\.com/@[A-Za-z0-9._-]+/video/\d+")


class SessionInvalid(RuntimeError):
    pass


def normalize(url: str) -> str | None:
    if not url:
        return None
    m = VIDEO_RE.search(url)
    return m.group(0) if m else None


def storage_states() -> list[Path]:
    raw = os.environ.get("TIKTOK_STORAGE_STATE_FILES", "")
    return [Path(p) for p in raw.split(":") if p and Path(p).is_file()]


def session_valid(page) -> bool:
    url = page.url.lower()
    title = (page.title() or "").lower()
    if "/login" in url or "ログイン" in title or "log in" in title or "login" in title:
        return False
    try:
        body = page.locator("body").inner_text(timeout=3000).lower()
        if "ログインして続行" in body or "log in to continue" in body:
            return False
    except Exception:
        pass
    return True


def discover_with_state(browser, state: Path, query: str, max_scrolls: int, max_results: int) -> tuple[list[dict], dict]:
    search_url = f"https://www.tiktok.com/search?q={quote(query)}"
    context = browser.new_context(
        storage_state=str(state),
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        locale="ja-JP",
        viewport={"width": 1440, "height": 1800},
    )
    page = context.new_page()
    try:
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        if not session_valid(page):
            raise SessionInvalid("TikTok storageState is invalid or login is required")

        body_sample = ""
        try:
            body_sample = page.locator("body").inner_text(timeout=5000)[:3000]
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
                    found.append({"url": url, "query": query, "source": "tiktok_browser_logged_in"})
                    if len(found) >= max_results:
                        break
            if len(found) >= max_results:
                break
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(1800)

        return found, {
            "search_url": search_url,
            "title": page.title(),
            "final_url": page.url,
            "body_sample": body_sample,
            "session_file": state.name,
        }
    finally:
        context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True)
    parser.add_argument("--max-scrolls", type=int, default=6)
    parser.add_argument("--max-results", type=int, default=50)
    parser.add_argument("--output", default="tiktok_browser_discovery.json")
    args = parser.parse_args()

    states = storage_states()
    if not states:
        raise SystemExit("No TikTok storageState files configured")

    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    results = []
    diagnostics = []
    errors = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        active_idx = 0

        for query in queries:
            success = False
            idx = active_idx
            while idx < len(states):
                try:
                    items, diag = discover_with_state(browser, states[idx], query, args.max_scrolls, args.max_results)
                    active_idx = idx
                    diagnostics.append({"query": query, **diag, "count": len(items), "session_index": idx + 1})
                    for item in items:
                        if item["url"] not in seen:
                            seen.add(item["url"])
                            results.append(item)
                    success = True
                    break
                except SessionInvalid as exc:
                    errors.append({"query": query, "session": idx + 1, "error": str(exc)})
                    idx += 1
                except Exception as exc:
                    errors.append({"query": query, "session": idx + 1, "error": str(exc)})
                    break
            if not success:
                diagnostics.append({"query": query, "count": 0, "status": "no_valid_session"})

        browser.close()

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "configured_sessions": len(states),
        "result_count": len(results),
        "results": results,
        "diagnostics": diagnostics,
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"configured_sessions": len(states), "result_count": len(results), "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
