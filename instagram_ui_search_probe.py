import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"


def storage_state():
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    for p in raw.split(":"):
        if p and Path(p).is_file():
            return p
    raise SystemExit("No Instagram storageState files configured")


def body_text(page):
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="ティーフレックス")
    ap.add_argument("--output", default="instagram_ui_search_probe.json")
    args = ap.parse_args()

    result = {"keyword": args.keyword, "actions": [], "links": [], "graphql_urls": [], "final_url": None, "title": None, "body_sample": None}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=storage_state(), locale="ja-JP", viewport={"width":1440,"height":1800}, user_agent=USER_AGENT)
        page = context.new_page()

        def on_response(resp):
            if "instagram.com" in resp.url and "graphql" in resp.url and resp.status == 200 and len(result["graphql_urls"]) < 30:
                result["graphql_urls"].append(resp.url[:1000])

        page.on("response", on_response)
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(6000)
        result["actions"].append({"step":"home","url":page.url,"title":page.title()})

        # Instagram may show a remembered-account continuation screen even when cookies exist.
        initial_body = body_text(page)
        if ("別のプロフィールを使用" in initial_body or "Use another profile" in initial_body) and ("次へ" in initial_body or "Continue" in initial_body):
            for label in ["次へ", "Continue"]:
                try:
                    loc = page.get_by_text(label, exact=True)
                    if loc.count():
                        loc.first.click(timeout=5000)
                        result["actions"].append({"step":"click_remembered_account_continue","label":label})
                        page.wait_for_timeout(7000)
                        break
                except Exception as exc:
                    result["actions"].append({"step":"continue_error","label":label,"error":str(exc)[:300]})

        clicked = False
        for label in ["検索", "Search"]:
            try:
                loc = page.get_by_text(label, exact=True)
                if loc.count():
                    loc.first.click(timeout=5000)
                    clicked = True
                    result["actions"].append({"step":"click_search_text","label":label})
                    break
            except Exception as exc:
                result["actions"].append({"step":"click_search_text_error","label":label,"error":str(exc)[:300]})

        if not clicked:
            for selector in ['a[aria-label*="検索"]','a[aria-label*="Search"]','div[role="button"][aria-label*="検索"]','div[role="button"][aria-label*="Search"]']:
                try:
                    loc = page.locator(selector)
                    if loc.count():
                        loc.first.click(timeout=5000)
                        clicked = True
                        result["actions"].append({"step":"click_search_selector","selector":selector})
                        break
                except Exception as exc:
                    result["actions"].append({"step":"click_search_selector_error","selector":selector,"error":str(exc)[:300]})

        page.wait_for_timeout(2500)

        input_loc = None
        for selector in ['input[placeholder="検索"]','input[placeholder="Search"]','input[aria-label="検索入力"]','input[aria-label="Search input"]','input[type="text"]']:
            try:
                loc = page.locator(selector)
                if loc.count():
                    input_loc = loc.first
                    result["actions"].append({"step":"found_input","selector":selector})
                    break
            except Exception:
                pass

        if input_loc is not None:
            input_loc.fill(args.keyword)
            result["actions"].append({"step":"filled","keyword":args.keyword})
            page.wait_for_timeout(7000)
            page.mouse.wheel(0, 1800)
            page.wait_for_timeout(2500)
        else:
            result["actions"].append({"step":"no_search_input"})

        seen = set()
        anchors = page.locator('a[href*="/p/"], a[href*="/reel/"]')
        for i in range(min(anchors.count(), 100)):
            href = anchors.nth(i).get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://www.instagram.com" + href
            href = href.split("?")[0]
            if href and href not in seen:
                seen.add(href)
                result["links"].append(href)

        result["final_url"] = page.url
        result["title"] = page.title()
        result["body_sample"] = body_text(page)[:8000]

        context.close()
        browser.close()

    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"UI post links: {len(result['links'])}")
    print(f"GraphQL responses: {len(result['graphql_urls'])}")
    print(f"Final URL: {result['final_url']}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
