import argparse
import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def parse_csv(raw):
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def storage_state():
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    for p in raw.split(":"):
        if p and Path(p).is_file():
            return p
    raise SystemExit("No Instagram storageState files configured")


def first_present(d, keys):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def caption_text(node):
    cap = node.get("caption")
    if isinstance(cap, dict):
        return str(cap.get("text") or "")
    if isinstance(cap, str):
        return cap
    edge = node.get("edge_media_to_caption")
    if isinstance(edge, dict):
        edges = edge.get("edges") or []
        if edges and isinstance(edges[0], dict):
            n = edges[0].get("node") or {}
            return str(n.get("text") or "")
    return ""


def username_from(node):
    owner = node.get("owner")
    if isinstance(owner, dict) and owner.get("username"):
        return str(owner["username"])
    user = node.get("user")
    if isinstance(user, dict) and user.get("username"):
        return str(user["username"])
    return ""


def count_from(node, direct_key, edge_key):
    value = node.get(direct_key)
    if isinstance(value, int):
        return value
    edge = node.get(edge_key)
    if isinstance(edge, dict):
        count = edge.get("count")
        if isinstance(count, int):
            return count
    return None


def normalize_media_node(node):
    code = first_present(node, ["code", "shortcode"])
    taken_at = first_present(node, ["taken_at", "taken_at_timestamp"])
    if not code or not isinstance(taken_at, (int, float)):
        return None
    dt = datetime.fromtimestamp(taken_at, tz=timezone.utc)
    text = caption_text(node)
    media_type = node.get("media_type")
    typename = str(node.get("__typename") or "")
    post_type = "reel" if media_type == 2 or "Video" in typename else "feed"
    return {
        "url": f"https://www.instagram.com/reel/{code}/" if post_type == "reel" else f"https://www.instagram.com/p/{code}/",
        "code": str(code),
        "type": post_type,
        "posted_at": dt.isoformat(),
        "date_source": "graphql_taken_at",
        "account": username_from(node),
        "likes": count_from(node, "like_count", "edge_media_preview_like"),
        "comments": count_from(node, "comment_count", "edge_media_to_comment"),
        "description": text[:3000],
    }


def walk_media(obj, out, depth=0):
    if depth > 14:
        return
    if isinstance(obj, dict):
        item = normalize_media_node(obj)
        if item:
            out.append(item)
        for v in obj.values():
            walk_media(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:300]:
            walk_media(v, out, depth + 1)


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

    keywords = parse_csv(args.keywords)
    anchors = parse_csv(args.anchor_keywords)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    state = storage_state()

    raw_items = []
    search_sources = {}
    graphql_responses = 0
    search_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=state,
            locale="ja-JP",
            viewport={"width": 1440, "height": 1800},
            user_agent=USER_AGENT,
        )
        page = context.new_page()

        current_keyword = {"value": ""}

        def on_response(resp):
            nonlocal graphql_responses
            url = resp.url
            if "instagram.com" not in url or "graphql" not in url or resp.status != 200:
                return
            try:
                data = resp.json()
            except Exception:
                return
            graphql_responses += 1
            before = len(raw_items)
            walk_media(data, raw_items)
            for item in raw_items[before:]:
                search_sources.setdefault(item["code"], set()).add(current_keyword["value"])

        page.on("response", on_response)

        for keyword in keywords:
            current_keyword["value"] = keyword
            urls = [
                f"https://www.instagram.com/explore/search/keyword/?q={quote(keyword)}",
                f"https://www.instagram.com/explore/tags/{quote(keyword.lstrip('#'))}/",
            ]
            for search_url in urls:
                try:
                    page.goto(search_url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(5000)
                    page.mouse.wheel(0, 2200)
                    page.wait_for_timeout(2500)
                except Exception as exc:
                    search_errors.append({"keyword": keyword, "url": search_url, "error": str(exc)})

        context.close()
        browser.close()

    dedup = {}
    for item in raw_items:
        code = item["code"]
        prev = dedup.get(code)
        if not prev:
            dedup[code] = item
        else:
            if not prev.get("description") and item.get("description"):
                dedup[code] = item

    all_items = list(dedup.values())
    all_items.sort(key=lambda x: x["posted_at"], reverse=True)
    candidate_items = all_items[: max(0, args.max_candidates)]

    accepted = []
    skip = {"outside_window": 0, "future_date": 0, "irrelevant_recent": 0, "non_japanese": 0}

    for item in candidate_items:
        dt = datetime.fromisoformat(item["posted_at"])
        if dt > now + timedelta(days=1):
            skip["future_date"] += 1
            continue
        if dt < cutoff:
            skip["outside_window"] += 1
            continue

        desc = item.get("description") or ""
        discovered = sorted(search_sources.get(item["code"], set()))
        item["discovered_by_keywords"] = discovered
        item["matched_keywords"] = matched_terms(desc, keywords)
        item["matched_anchor_keywords"] = matched_terms(desc, anchors)
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

        accepted.append(item)
        if len(accepted) >= args.max_posts:
            break

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "collector_version": "v5_graphql",
        "days": args.days,
        "keywords": keywords,
        "anchor_keywords": anchors,
        "graphql_response_count": graphql_responses,
        "graphql_media_node_count": len(raw_items),
        "candidate_count": len(all_items),
        "candidate_check_limit": args.max_candidates,
        "checked_candidate_count": min(len(all_items), args.max_candidates),
        "accepted_post_count": len(accepted),
        "skip_reasons": skip,
        "posts": accepted,
        "errors": search_errors,
    }

    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"GraphQL responses: {graphql_responses}")
    print(f"GraphQL media nodes: {len(raw_items)}")
    print(f"Unique candidates: {len(all_items)}")
    print(f"Checked candidates: {payload['checked_candidate_count']}")
    print(f"Accepted relevant posts: {len(accepted)}")
    print(f"Skip reasons: {skip}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
