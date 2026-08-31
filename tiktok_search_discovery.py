import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TIKTOK_VIDEO_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9._-]+/video/\d+")


def fetch(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.8,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        return res.read().decode("utf-8", errors="ignore")


def normalize(url: str) -> str | None:
    url = html.unescape(url or "")
    url = urllib.parse.unquote(url)
    m = TIKTOK_VIDEO_RE.search(url)
    return m.group(0).split("?")[0].split("#")[0] if m else None


def extract_tiktok_urls(page: str, source: str, query: str, max_results: int) -> list[dict]:
    found = []
    seen = set()
    decoded_page = html.unescape(page)

    for match in TIKTOK_VIDEO_RE.findall(decoded_page):
        url = normalize(match)
        if url and url not in seen:
            seen.add(url)
            found.append({"url": url, "source": source, "query": query})
            if len(found) >= max_results:
                return found

    for href in re.findall(r'href=["\']([^"\']+)["\']', decoded_page):
        url = normalize(href)
        if url and url not in seen:
            seen.add(url)
            found.append({"url": url, "source": source, "query": query})
            if len(found) >= max_results:
                return found
    return found


def ddg_search(query: str, max_results: int) -> list[dict]:
    endpoint = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    page = fetch(endpoint)
    found = extract_tiktok_urls(page, "duckduckgo", query, max_results)

    # DuckDuckGo redirect links contain the target in uddg=.
    seen = {x["url"] for x in found}
    for href in re.findall(r'href=["\']([^"\']+)["\']', html.unescape(page)):
        if "uddg=" not in href:
            continue
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        for target in qs.get("uddg", []):
            url = normalize(target)
            if url and url not in seen:
                seen.add(url)
                found.append({"url": url, "source": "duckduckgo_redirect", "query": query})
                if len(found) >= max_results:
                    return found
    return found


def bing_search(query: str, max_results: int) -> list[dict]:
    endpoint = "https://www.bing.com/search?" + urllib.parse.urlencode({"q": query, "count": 50, "setlang": "ja-jp"})
    return extract_tiktok_urls(fetch(endpoint), "bing", query, max_results)


def yahoo_search(query: str, max_results: int) -> list[dict]:
    endpoint = "https://search.yahoo.co.jp/search?" + urllib.parse.urlencode({"p": query, "ei": "UTF-8"})
    return extract_tiktok_urls(fetch(endpoint), "yahoo_japan", query, max_results)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True, help="Comma-separated search queries")
    parser.add_argument("--max-results", type=int, default=30)
    parser.add_argument("--output", default="tiktok_search_discovery.json")
    args = parser.parse_args()

    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    results = []
    errors = []
    seen = set()
    engine_stats = {"duckduckgo": 0, "bing": 0, "yahoo_japan": 0}

    for query in queries:
        for engine_name, search_fn in [
            ("duckduckgo", ddg_search),
            ("bing", bing_search),
            ("yahoo_japan", yahoo_search),
        ]:
            try:
                items = search_fn(query, args.max_results)
                engine_stats[engine_name] += len(items)
                for item in items:
                    if item["url"] not in seen:
                        seen.add(item["url"])
                        results.append(item)
            except Exception as exc:
                errors.append({"query": query, "engine": engine_name, "error": str(exc)})
            time.sleep(1)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "queries": queries,
        "result_count": len(results),
        "engine_stats": engine_stats,
        "results": results,
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"result_count": len(results), "engine_stats": engine_stats, "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
