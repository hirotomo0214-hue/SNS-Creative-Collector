import argparse
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

INSTAGRAM_CODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)", re.I)
JST = timezone(timedelta(hours=9))


def canonical_post_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    m = INSTAGRAM_CODE_RE.search(raw)
    if m:
        return f"instagram:{m.group(1)}"
    return raw.rstrip("/").lower()


def score_post(post: dict) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    likes = post.get("likes")
    comments = post.get("comments")
    anchors = post.get("matched_anchor_keywords") or []

    if anchors:
        score += 2
        reasons.append("anchor_match")

    if isinstance(comments, int):
        if comments >= 50:
            score += 4
            reasons.append("comments_50_plus")
        elif comments >= 20:
            score += 3
            reasons.append("comments_20_plus")
        elif comments >= 5:
            score += 1
            reasons.append("comments_5_plus")

    if isinstance(likes, int):
        if likes >= 300:
            score += 3
            reasons.append("likes_300_plus")
        elif likes >= 100:
            score += 2
            reasons.append("likes_100_plus")
        elif likes >= 50:
            score += 1
            reasons.append("likes_50_plus")

    description = (post.get("description") or "").lower()
    if "コメント" in description or "comment" in description:
        score += 1
        reasons.append("explicit_comment_cta")
    if any(token in description for token in ["限定", "キャンペーン", "off", "半額"]):
        score += 1
        reasons.append("campaign_structure")

    return score, reasons


def load_known_keys(path: str | None) -> set[str]:
    if not path:
        return set()
    file_path = Path(path)
    if not file_path.exists():
        return set()
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        values = payload.get("urls", [])
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    return {canonical_post_key(value) for value in values if canonical_post_key(value)}


def acquisition_date(generated_at: str | None) -> str:
    if generated_at:
        try:
            dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            return dt.astimezone(JST).date().isoformat()
        except Exception:
            pass
    return datetime.now(JST).date().isoformat()


def build_notion_queue_item(post: dict, generated_at: str | None, project_hint: str) -> dict:
    account = str(post.get("account") or "").strip()
    posted_at = str(post.get("posted_at") or "")
    posted_date = posted_at[:10] if posted_at else ""
    url = str(post.get("url") or "").strip()
    post_key = canonical_post_key(url)
    acquired_date = acquisition_date(generated_at)
    title_date = acquired_date.replace("-", "")
    project = project_hint.strip()
    page_title = "_".join(part for part in [title_date, project, account] if part)
    return {
        "idempotency_key": post_key,
        "source_url": url,
        "status": "pending_live_duplicate_check",
        "target": "Notion [DB]インフルエンサー クリエイティブ収集",
        "acquisition_date": acquired_date,
        "page_title": page_title,
        "title_date_basis": "acquisition_date",
        "project_hint": project,
        "account_id": account,
        "properties": {
            "動画URL": url,
            "アカウントURL": f"https://www.instagram.com/{account}/" if account else "",
            "投稿日": posted_date,
            "取得元": "SNSバズ研究",
            "媒体": "Instagram",
            "投稿形態": "他社リール" if post.get("type") == "reel" else "他社フィード",
        },
        "observations": {
            "shortcode": post_key.split(":", 1)[1] if post_key.startswith("instagram:") else "",
            "likes": post.get("likes"),
            "comments": post.get("comments"),
            "research_score": post.get("research_score"),
            "research_reasons": post.get("research_reasons", []),
            "description": post.get("description", ""),
            "matched_anchor_keywords": post.get("matched_anchor_keywords", []),
        },
        "safety": {
            "require_live_notion_duplicate_check": True,
            "do_not_overwrite_existing": True,
            "causal_performance_claim_allowed": False,
            "notion_title_must_use_acquisition_date_not_posted_date": True,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="research_selection.json")
    parser.add_argument("--queue-output", default="notion_save_queue.json")
    parser.add_argument("--auto-threshold", type=int, default=5)
    parser.add_argument("--known-urls", default="")
    parser.add_argument("--project", default="")
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    known_keys = load_known_keys(args.known_urls)
    selected = []
    review = []
    duplicates = []
    seen_keys = set()

    for post in payload.get("posts", []):
        score, reasons = score_post(post)
        item = dict(post)
        item["research_score"] = score
        item["research_reasons"] = reasons
        url = str(item.get("url") or "").strip()
        post_key = canonical_post_key(url)
        item["canonical_post_key"] = post_key

        if post_key and post_key in seen_keys:
            item["research_decision"] = "duplicate_batch"
            item["research_reasons"] = reasons + ["shortcode_duplicate_in_batch"]
            duplicates.append(item)
            continue
        if post_key:
            seen_keys.add(post_key)

        if post_key and post_key in known_keys:
            item["research_decision"] = "duplicate_existing"
            item["research_reasons"] = reasons + ["notion_shortcode_duplicate"]
            duplicates.append(item)
        elif score >= args.auto_threshold:
            item["research_decision"] = "save_candidate"
            selected.append(item)
        else:
            item["research_decision"] = "manual_review"
            review.append(item)

    out = {
        "generated_at": payload.get("generated_at"),
        "source_candidate_count": payload.get("accepted_post_count", len(payload.get("posts", []))),
        "known_key_count": len(known_keys),
        "duplicate_existing_count": len(duplicates),
        "save_candidate_count": len(selected),
        "manual_review_count": len(review),
        "auto_threshold": args.auto_threshold,
        "save_candidates": selected,
        "duplicate_existing": duplicates,
        "manual_review": review,
        "policy": {
            "purpose": "Conservative research-value gate before Notion persistence",
            "notes": [
                "Instagram /p/, /reel/, and /tv/ URLs are normalized to the same shortcode key.",
                "Known Notion shortcode keys and duplicate shortcodes within the current batch are removed before persistence candidates are emitted.",
                "The local ledger is a safety cache; live Notion duplicate checking is still required immediately before write.",
                "Notion page titles use acquisition date in JST; 投稿日 remains the source post date property.",
                "A high score means research-worthy candidate, not proven causal performance."
            ]
        }
    }
    queue = {
        "generated_at": payload.get("generated_at"),
        "queue_version": 4,
        "idempotency_strategy": "instagram_shortcode",
        "title_date_basis": "acquisition_date",
        "project_hint": args.project.strip(),
        "pending_count": len(selected),
        "items": [build_notion_queue_item(item, payload.get("generated_at"), args.project) for item in selected],
    }

    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.queue_output).write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Known Notion keys: {len(known_keys)}")
    print(f"Duplicates removed: {len(duplicates)}")
    print(f"Save candidates: {len(selected)}")
    print(f"Manual review: {len(review)}")
    print(f"Saved selection: {args.output}")
    print(f"Saved Notion queue: {args.queue_output}")


if __name__ == "__main__":
    main()
