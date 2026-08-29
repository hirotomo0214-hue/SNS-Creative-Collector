import argparse
import json
import os
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
INTEREST_KEYS = {
    "code", "shortcode", "pk", "id", "taken_at", "taken_at_timestamp",
    "caption", "text", "like_count", "comment_count", "edge_media_preview_like",
    "edge_media_to_comment", "owner", "username", "media_type"
}


def storage_state():
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    for p in raw.split(":"):
        if p and Path(p).is_file():
            return p
    raise SystemExit("No Instagram storageState files configured")


def compact(obj, depth=0):
    if depth > 8:
        return None
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in INTEREST_KEYS:
                if k == "caption" and isinstance(v, dict):
                    out[k] = {kk: vv for kk, vv in v.items() if kk in {"text", "created_at", "pk", "id"}}
                elif k in {"owner"} and isinstance(v, dict):
                    out[k] = {kk: vv for kk, vv in v.items() if kk in {"username", "id", "pk"}}
                else:
                    out[k] = v
            else:
                c = compact(v, depth + 1)
                if c not in (None, {}, []):
                    out[k] = c
        return out
    if isinstance(obj, list):
        vals = []
        for v in obj[:100]:
            c = compact(v, depth + 1)
            if c not in (None, {}, []):
                vals.append(c)
        return vals
    return None


def collect_media_nodes(obj, path="$", out=None, depth=0):
    if out is None:
        out = []
    if depth > 12:
        return out
    if isinstance(obj, dict):
        keys = set(obj.keys())
        looks_media = bool(keys & {"code", "shortcode"}) and bool(keys & {"taken_at", "taken_at_timestamp", "caption", "like_count", "comment_count"})
        if looks_media:
            out.append({"path": path, "data": compact(obj)})
        for k, v in obj.items():
            collect_media_nodes(v, f"{path}.{k}", out, depth + 1)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:200]):
            collect_media_nodes(v, f"{path}[{i}]", out, depth + 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keyword", default="ティーフレックス")
    ap.add_argument("--output", default="instagram_graphql_probe.json")
    args = ap.parse_args()

    captured = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=storage_state(), locale="ja-JP",
            viewport={"width": 1440, "height": 1800}, user_agent=USER_AGENT,
        )
        page = context.new_page()

        def on_response(resp):
            if len(captured) >= 20:
                return
            url = resp.url
            if "instagram.com" not in url or "graphql" not in url or resp.status != 200:
                return
            try:
                data = resp.json()
                nodes = collect_media_nodes(data)
                captured.append({
                    "url": url[:500],
                    "media_node_count": len(nodes),
                    "media_nodes": nodes[:50],
                    "compact": compact(data),
                })
            except Exception as exc:
                captured.append({"url": url[:500], "error": f"{type(exc).__name__}: {exc}"})

        page.on("response", on_response)
        page.goto(f"https://www.instagram.com/explore/search/keyword/?q={quote(args.keyword)}", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)
        page.mouse.wheel(0, 2500)
        page.wait_for_timeout(4000)

        items = []
        anchors = page.locator('a[href*="/p/"], a[href*="/reel/"]')
        for i in range(min(anchors.count(), 10)):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            alt = ""
            try:
                img = a.locator("img").first
                if img.count():
                    alt = img.get_attribute("alt") or ""
            except Exception:
                pass
            items.append({"href": href, "alt": alt[:2000]})

        context.close()
        browser.close()

    payload = {"keyword": args.keyword, "search_items": items, "graphql": captured}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Search items: {len(items)}")
    print(f"GraphQL responses captured: {len(captured)}")
    print(f"GraphQL media nodes: {sum(x.get('media_node_count', 0) for x in captured)}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
