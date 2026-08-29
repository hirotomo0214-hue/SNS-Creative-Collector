import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def storage_state():
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    for p in raw.split(":"):
        if p and Path(p).is_file():
            return p
    raise SystemExit("No Instagram storageState files configured")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="ティーフレックス")
    ap.add_argument("--max-items", type=int, default=10)
    ap.add_argument("--output", default="instagram_search_probe.json")
    args = ap.parse_args()

    result = {"keyword": args.keyword, "items": [], "network": []}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=storage_state(),
            locale="ja-JP",
            viewport={"width": 1440, "height": 1800},
            user_agent=USER_AGENT,
        )
        page = context.new_page()

        def on_response(resp):
            url = resp.url
            if "instagram.com" in url and any(token in url for token in ["graphql", "api/v1", "search", "web_profile_info"]):
                if len(result["network"]) < 40:
                    result["network"].append({"status": resp.status, "url": url[:500]})

        page.on("response", on_response)
        search_url = f"https://www.instagram.com/explore/search/keyword/?q={quote(args.keyword)}"
        page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(7000)

        anchors = page.locator('a[href*="/p/"], a[href*="/reel/"]')
        count = min(anchors.count(), args.max_items)
        for i in range(count):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            try:
                outer = a.evaluate("el => el.outerHTML")
            except Exception:
                outer = ""
            try:
                parent_text = a.evaluate("el => (el.parentElement?.innerText || '').slice(0,1000)")
            except Exception:
                parent_text = ""
            try:
                parent_html = a.evaluate("el => (el.parentElement?.outerHTML || '').slice(0,3000)")
            except Exception:
                parent_html = ""
            result["items"].append({
                "href": href,
                "anchor_outer_html": outer[:3000],
                "parent_text": parent_text,
                "parent_html": parent_html,
            })

        result["page_title"] = page.title()
        try:
            result["body_preview"] = page.locator("body").inner_text()[:3000]
        except Exception:
            result["body_preview"] = ""

        context.close()
        browser.close()

    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Probe items: {len(result['items'])}")
    print(f"Network candidates: {len(result['network'])}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
