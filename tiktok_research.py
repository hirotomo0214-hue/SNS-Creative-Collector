import argparse
import json
import re
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

VIDEO_RE = re.compile(r"^https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9._-]+/video/\d+")


def normalize_url(url: str) -> str | None:
    if not url:
        return None
    url = url.split("?")[0].split("#")[0]
    m = VIDEO_RE.match(url)
    return m.group(0) if m else None


def ytdlp_json(url: str, flat: bool = False) -> dict:
    cmd = ["yt-dlp", "--dump-single-json", "--no-warnings"]
    if flat:
        cmd.append("--flat-playlist")
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yt-dlp failed")
    return json.loads(proc.stdout)


def profile_posts(profile_url: str, max_candidates: int) -> list[str]:
    data = ytdlp_json(profile_url, flat=True)
    urls = []
    for entry in data.get("entries") or []:
        url = normalize_url(entry.get("webpage_url") or entry.get("url") or "")
        if not url and entry.get("id"):
            account = profile_url.rstrip("/").split("@")[-1]
            url = f"https://www.tiktok.com/@{account}/video/{entry['id']}"
        if url and url not in urls:
            urls.append(url)
        if len(urls) >= max_candidates:
            break
    return urls


def inspect_post(url: str, keywords: list[str], cutoff: datetime) -> tuple[dict | None, str | None]:
    data = ytdlp_json(url)
    timestamp = data.get("timestamp") or data.get("release_timestamp")
    if not timestamp:
        return None, "date_missing"
    posted = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    if posted < cutoff:
        return None, "outside_window"
    description = data.get("description") or data.get("title") or ""
    haystack = description.lower()
    matched = [k for k in keywords if k.lower() in haystack]
    if not matched:
        return None, "keyword_miss"
    uploader = data.get("uploader_id") or data.get("uploader") or ""
    return {
        "url": normalize_url(data.get("webpage_url") or url) or url,
        "type": "video",
        "posted_at": posted.isoformat(),
        "account": uploader,
        "description": description[:1500],
        "view_count": data.get("view_count"),
        "like_count": data.get("like_count"),
        "comment_count": data.get("comment_count"),
        "repost_count": data.get("repost_count"),
        "matched_keywords": matched,
    }, None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True, help="Comma-separated TikTok profile URLs")
    parser.add_argument("--keywords", required=True, help="Comma-separated discovery keywords")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--max-posts", type=int, default=30)
    parser.add_argument("--output", default="tiktok_research.json")
    args = parser.parse_args()

    profiles = [x.strip() for x in args.profiles.split(",") if x.strip()]
    keywords = [x.strip() for x in args.keywords.split(",") if x.strip()]
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    candidates = []
    errors = []
    skip_reasons = {"date_missing": 0, "outside_window": 0, "keyword_miss": 0}

    for profile in profiles:
        try:
            for url in profile_posts(profile, args.max_candidates):
                if url not in candidates:
                    candidates.append(url)
        except Exception as exc:
            errors.append({"stage": "profile", "profile": profile, "error": str(exc)})

    posts = []
    for url in candidates:
        if len(posts) >= args.max_posts:
            break
        try:
            item, reason = inspect_post(url, keywords, cutoff)
            if reason:
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            elif item:
                posts.append(item)
        except Exception as exc:
            errors.append({"stage": "post", "url": url, "error": str(exc)})

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
        "keywords": keywords,
        "days": args.days,
        "candidate_count": len(candidates),
        "recent_post_count": len(posts),
        "skip_reasons": skip_reasons,
        "posts": sorted(posts, key=lambda x: x["posted_at"], reverse=True),
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidate_count": len(candidates), "recent_post_count": len(posts), "skip_reasons": skip_reasons, "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
