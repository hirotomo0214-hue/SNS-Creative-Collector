import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from research_selector import canonical_post_key

NOTION_VERSION = "2025-09-03"


def api_request(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.notion.com/v1{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Notion API {exc.code}: {body}") from exc


def extract_page_post_url(page: dict) -> str:
    prop = (page.get("properties") or {}).get("動画URL") or {}
    return str(prop.get("url") or "").strip() if isinstance(prop, dict) else ""


def load_existing_post_keys(token: str, data_source_id: str) -> set[str]:
    keys: set[str] = set(); cursor = None
    while True:
        payload: dict = {"page_size": 100}
        if cursor: payload["start_cursor"] = cursor
        result = api_request("POST", f"/data_sources/{data_source_id}/query", token, payload)
        for page in result.get("results", []):
            key = canonical_post_key(extract_page_post_url(page))
            if key: keys.add(key)
        if not result.get("has_more"): break
        cursor = result.get("next_cursor")
        if not cursor: break
    return keys


def queue_item_post_key(item: dict) -> str:
    raw = str(item.get("idempotency_key") or "").strip()
    if raw.startswith("instagram:"):
        return raw
    key = canonical_post_key(raw)
    if key: return key
    source_url = str(item.get("source_url") or (item.get("properties") or {}).get("動画URL") or "").strip()
    return canonical_post_key(source_url)


def make_properties(item: dict) -> dict:
    props = item.get("properties", {})
    url = props.get("動画URL", ""); account_url = props.get("アカウントURL", ""); posted_date = props.get("投稿日", "")
    account = account_url.rstrip("/").split("/")[-1] if account_url else str(item.get("account_id") or "unknown")
    acquired = str(item.get("acquisition_date") or "").replace("-", "")
    title = f"{acquired or posted_date.replace('-', '')}_SNS研究_{account}"
    notion_props: dict = {
        "名前": {"title": [{"text": {"content": title}}]}, "動画URL": {"url": url or None}, "アカウントURL": {"url": account_url or None},
        "取得元": {"select": {"name": "SNSバズ研究"}}, "媒体": {"select": {"name": "Instagram"}},
        "投稿形態": {"select": {"name": props.get("投稿形態") or "他社フィード"}},
    }
    if posted_date: notion_props["投稿日"] = {"date": {"start": posted_date}}
    project_hint = str(item.get("project_hint") or "").strip()
    if project_hint: notion_props["案件"] = {"multi_select": [{"name": project_hint}]}
    return notion_props


def make_children(item: dict) -> list[dict]:
    obs = item.get("observations", {}); likes = obs.get("likes"); comments = obs.get("comments"); score = obs.get("research_score")
    reasons = ", ".join(obs.get("research_reasons") or []); description = str(obs.get("description") or "")[:1800]
    text = (f"観測事実\nいいね: {likes if likes is not None else '未取得'} / コメント: {comments if comments is not None else '未取得'}\n"
            f"研究スコア: {score if score is not None else '未算出'}\n採用根拠: {reasons or 'なし'}\n\nInstagram公開メタデータ\n{description}\n\n"
            "注意: 公開反応数は成果・CVとの因果を示すものではありません。")
    return [{"object": "block", "type": "paragraph", "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]}}]


def create_page(token: str, data_source_id: str, item: dict) -> dict:
    return api_request("POST", "/pages", token, {"parent": {"type": "data_source_id", "data_source_id": data_source_id}, "properties": make_properties(item), "children": make_children(item)})


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("--queue", required=True); parser.add_argument("--result", default="notion_persist_result.json"); parser.add_argument("--dry-run", action="store_true"); args = parser.parse_args()
    queue = json.loads(Path(args.queue).read_text(encoding="utf-8")); token = os.environ.get("NOTION_TOKEN", "").strip(); data_source_id = os.environ.get("NOTION_DATA_SOURCE_ID", "").strip()
    if not args.dry_run and (not token or not data_source_id):
        result = {"status": "blocked_missing_credentials", "created": [], "duplicates": [], "errors": [], "pending_count": len(queue.get("items", []))}
        Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"); print("Notion credentials are not configured; persistence is skipped safely."); return
    created=[]; duplicates=[]; errors=[]; existing_keys=set() if args.dry_run else load_existing_post_keys(token, data_source_id); seen_queue_keys=set()
    for item in queue.get("items", []):
        post_key=queue_item_post_key(item); source_url=str(item.get("source_url") or (item.get("properties") or {}).get("動画URL") or "").strip()
        if not post_key: errors.append({"url": source_url, "error": "missing_idempotency_key"}); continue
        if post_key in seen_queue_keys: duplicates.append({"key": post_key, "url": source_url, "reason": "duplicate_in_queue"}); continue
        seen_queue_keys.add(post_key)
        if args.dry_run: created.append({"key": post_key, "url": source_url, "dry_run": True}); continue
        try:
            if post_key in existing_keys: duplicates.append({"key": post_key, "url": source_url, "reason": "duplicate_existing_notion"}); continue
            page=create_page(token, data_source_id, item); created.append({"key": post_key, "url": source_url, "page_id": page.get("id"), "page_url": page.get("url")}); existing_keys.add(post_key)
        except Exception as exc: errors.append({"key": post_key, "url": source_url, "error": str(exc)})
    result={"status": "dry_run" if args.dry_run else ("success" if not errors else "partial_failure"), "created": created, "duplicates": duplicates, "errors": errors, "pending_count": len(queue.get("items", [])), "idempotency_strategy": "canonical_post_key"}
    Path(args.result).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"); print(json.dumps(result, ensure_ascii=False))
    if errors and not args.dry_run: sys.exit(2)


if __name__ == "__main__": main()
