import argparse
import json
from pathlib import Path


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="research_selection.json")
    parser.add_argument("--auto-threshold", type=int, default=5)
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    selected = []
    review = []

    for post in payload.get("posts", []):
        score, reasons = score_post(post)
        item = dict(post)
        item["research_score"] = score
        item["research_reasons"] = reasons
        if score >= args.auto_threshold:
            item["research_decision"] = "save_candidate"
            selected.append(item)
        else:
            item["research_decision"] = "manual_review"
            review.append(item)

    out = {
        "generated_at": payload.get("generated_at"),
        "source_candidate_count": payload.get("accepted_post_count", len(payload.get("posts", []))),
        "save_candidate_count": len(selected),
        "manual_review_count": len(review),
        "auto_threshold": args.auto_threshold,
        "save_candidates": selected,
        "manual_review": review,
        "policy": {
            "purpose": "Conservative research-value gate before Notion persistence",
            "notes": [
                "This stage does not write to Notion.",
                "Notion URL duplicate checking remains mandatory before persistence.",
                "A high score means research-worthy candidate, not proven causal performance."
            ]
        }
    }
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Save candidates: {len(selected)}")
    print(f"Manual review: {len(review)}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
