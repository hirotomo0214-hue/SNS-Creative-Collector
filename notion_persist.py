import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

NOTION_VERSION = "2025-09-03"


def api_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.notion.com/v1{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API {exc.code}: {body}") from exc


def live_duplicate_exists(token: str, data_source_id: str, url: str) -> bool:
    payload = {
        "filter": {
            "property": "動画URL",
            "url": {"equals": url},
        },
        "page_size": 1,
    }
    result = api_request("POST", f"/data_sources/{data_source_id}/query", token, payload)
    return bool(result.get("results"))


def make_properties(item: dict) -> dict:
    props = item.get("properties", {})
    url = props.get("動画URL", "")
    account_url = props.get("アカウントURL", "")
    posted_date = props.get("投稿日", "")
    account = account_url.rstrip("/").split("/")[-1] if account_url else "unknown"
    title = f"{posted_date.replace('-', '')}_SNS研究_{account}" if posted_date else f"SNS研究_{account}"

    notion_props: dict = {
        "名前": {"title": [{"text": {"content": title}}]},
        "動画URL": {"url": url or None},
        "アカウントURL": {"url": account_url or None},
        "取得元": {"select": {"name": "SNSバズ研究"}},
        "媒体": {"select": {"name": "Instagram"}},
        "投稿形態": {"select": {"name": props.get("投稿形態") or "他社フィード"}},
    }
    if posted_date:
        notion_props["投稿日"] = {"date": {"start": posted_date}}
    return notion_props


def make_children(item: dict) -> list[dict]:
    obs = item.get("observations", {})
    likes = obs.get("likes")
    comments = obs.get("comments")
    score = obs.get("research_score")
    reasons = ", ".join(obs.get("research_reasons") or [])
    description = str(obs.get("description") or "")[:1800]
    text = (
        f"観測事実\n"
        f"いいね: {likes if likes is not None else '未取得'} / コメント: {comments if comments is not None else '未取得'}\n"
        f"研究スコア: {score if score is not None else '未算出'}\n"
        f"採用根拠: {reasons or 'なし'}\n\n"
        f"Instagram公開メタデータ\n{description}\n\n"
        "注意: 公開反応数は成果・CVとの因果を示すものではありません。"
    )
    return [
        {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
        }
    ]


def create_page(token: str, data_source_id: str, item: dict) -> dict:
    return api_request(
        "POST",
        "/pages",
        token,
        {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": make_properties(item),
            "children": make_children(item),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", required=True)
    parser.add_argument("--result", default="notion_persist_result.json")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    queue = json.loads(Path(args.queue).read_text(encoding="utf-8"))
    token = os.environ.get("NOTION_TOKEN", "").strip()
    data_source_id = os.environ.get("NOTION_DATA_SOURCE_ID", "").strip()

    if not args.dry_run and (not token or not data_source_id):
        print("Notion credentials are not configured; persistence is skipped safely.")
        result = {
            "status": "blocked_missing_credentials",
            "created": [],
            "duplicates": [],
            "errors": [],
            "pending_count": len(queue.get("items", [])),
        }
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return

    created = []
    duplicates = []
    errors = []

    for item in queue.get("items", []):
        url = str(item.get("idempotency_key") or "").strip()
        if not url:
            errors.append({"url": url, "error": "missing_idempotency_key"})
            continue
        if args.dry_run:
            created.append({"url": url, "dry_run": True})
            continue
        try:
            if live_duplicate_exists(token, data_source_id, url):
                duplicates.append(url)
                continue
            page = create_page(token, data_source_id, item)
            created.append({"url": url, "page_id": page.get("id"), "page_url": page.get("url")})
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})

    result = {
        "status": "dry_run" if args.dry_run else ("success" if not errors else "partial_failure"),
        "created": created,
        "duplicates": duplicates,
        "errors": errors,
        "pending_count": len(queue.get("items", [])),
    }
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    if errors and not args.dry_run:
        sys.exit(2)


if __name__ == "__main__":
    main()
