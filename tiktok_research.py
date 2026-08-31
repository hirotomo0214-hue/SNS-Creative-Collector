import argparse
import json
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

VIDEO_RE = re.compile(r"^https?://(?:www\.)?tiktok\.com/@[A-Za-z0-9._-]+/video/\d+")


def normalize_url(url: str) -> str | None:
    if not url:
        return None
    url = url.split("?")[0].split("#")[0]
    m = VIDEO_RE.match(url)
    return m.group(0) if m else None


def video_id_from_url(url: str) -> str | None:
    m = re.search(r"/video/(\d+)", url or "")
    return m.group(1) if m else None


def derived_posted_at(url: str) -> datetime | None:
    video_id = video_id_from_url(url)
    if not video_id:
        return None
    try:
        ts = int(video_id) >> 32
        posted = datetime.fromtimestamp(ts, tz=timezone.utc)
        if 2016 <= posted.year <= datetime.now(timezone.utc).year + 1:
            return posted
    except (ValueError, OSError, OverflowError):
        return None
    return None


def ytdlp_json(url: str, flat: bool = False) -> dict:
    cmd = ["yt-dlp", "--dump-single-json", "--no-warnings"]
    if flat:
        cmd.append("--flat-playlist")
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "yt-dlp failed")
    return json.loads(proc.stdout)


def tiktok_oembed(url: str) -> dict:
    endpoint = "https://www.tiktok.com/oembed?" + urllib.parse.urlencode({"url": url})
    req = urllib.request.Request(endpoint, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as res:
        return json.loads(res.read().decode("utf-8"))


def profile_posts(profile_url: str, max_candidates: int) -> list[dict]:
    data = ytdlp_json(profile_url, flat=True)
    items = []
    seen = set()
    account = profile_url.rstrip("/").split("@")[-1]
    for entry in data.get("entries") or []:
        url = normalize_url(entry.get("webpage_url") or entry.get("url") or "")
        if not url and entry.get("id"):
            url = f"https://www.tiktok.com/@{account}/video/{entry['id']}"
        if not url or url in seen:
            continue
        seen.add(url)
        timestamp = entry.get("timestamp") or entry.get("release_timestamp")
        posted = datetime.fromtimestamp(int(timestamp), tz=timezone.utc) if timestamp else derived_posted_at(url)
        items.append({
            "url": url,
            "account": entry.get("uploader_id") or entry.get("uploader") or account,
            "description": entry.get("description") or entry.get("title") or "",
            "posted_at": posted.isoformat() if posted else None,
            "date_source": "profile_metadata" if timestamp else ("video_id_derived" if posted else None),
            "view_count": entry.get("view_count"),
            "like_count": entry.get("like_count"),
            "comment_count": entry.get("comment_count"),
            "repost_count": entry.get("repost_count"),
        })
        if len(items) >= max_candidates:
            break
    return items


def inspect_post(candidate: dict, keywords: list[str], cutoff: datetime) -> tuple[dict | None, str | None, str | None, dict | None]:
    url = candidate["url"]
    data = None
    source = "profile_metadata"
    ytdlp_error = None

    try:
        data = ytdlp_json(url)
        source = "yt_dlp_post"
    except Exception as exc:
        ytdlp_error = str(exc)
        try:
            data = tiktok_oembed(url)
            source = "tiktok_oembed"
        except Exception as oembed_exc:
            return None, "metadata_unavailable", f"yt-dlp: {ytdlp_error} | oEmbed: {oembed_exc}", None

    description = (
        (data.get("description") if source == "yt_dlp_post" else None)
        or data.get("title")
        or candidate.get("description")
        or ""
    )
    account = (
        (data.get("uploader_id") or data.get("uploader") if source == "yt_dlp_post" else None)
        or data.get("author_name")
        or candidate.get("account")
        or ""
    )

    timestamp = None
    if source == "yt_dlp_post":
        timestamp = data.get("timestamp") or data.get("release_timestamp")
    if timestamp:
        posted = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
        date_source = "yt_dlp_post"
    elif candidate.get("posted_at"):
        posted = datetime.fromisoformat(candidate["posted_at"])
        date_source = candidate.get("date_source") or "profile_metadata"
    else:
        posted = derived_posted_at(url)
        date_source = "video_id_derived" if posted else None

    if not posted:
        return None, "date_missing", ytdlp_error, None

    candidate_record = {
        "url": url,
        "posted_at": posted.isoformat(),
        "date_source": date_source,
        "account": account,
        "description": description[:1500],
        "view_count": data.get("view_count") if source == "yt_dlp_post" else candidate.get("view_count"),
        "like_count": data.get("like_count") if source == "yt_dlp_post" else candidate.get("like_count"),
        "comment_count": data.get("comment_count") if source == "yt_dlp_post" else candidate.get("comment_count"),
        "repost_count": data.get("repost_count") if source == "yt_dlp_post" else candidate.get("repost_count"),
        "metadata_source": source,
    }

    if posted < cutoff:
        return None, "outside_window", ytdlp_error, candidate_record

    haystack = description.lower()
    matched = [k for k in keywords if k.lower() in haystack]
    if not matched:
        return None, "keyword_miss", ytdlp_error, candidate_record

    item = dict(candidate_record)
    item["type"] = "video"
    item["matched_keywords"] = matched
    item["yt_dlp_post_error"] = ytdlp_error
    return item, None, None, candidate_record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profiles", required=True)
    parser.add_argument("--keywords", required=True)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--max-posts", type=int, default=30)
    parser.add_argument("--output", default="tiktok_research.json")
    args = parser.parse_args()

    profiles = [x.strip() for x in args.profiles.split(",") if x.strip()]
    keywords = [x.strip() for x in args.keywords.split(",") if x.strip()]
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    candidates = []
    seen = set()
    errors = []
    skip_reasons = {"date_missing": 0, "outside_window": 0, "keyword_miss": 0, "metadata_unavailable": 0}

    for profile in profiles:
        try:
            for candidate in profile_posts(profile, args.max_candidates):
                if candidate["url"] not in seen:
                    seen.add(candidate["url"])
                    candidates.append(candidate)
        except Exception as exc:
            errors.append({"stage": "profile", "profile": profile, "error": str(exc)})

    posts = []
    recent_candidates = []
    for candidate in candidates:
        item, reason, error, candidate_record = inspect_post(candidate, keywords, cutoff)
        if candidate_record and datetime.fromisoformat(candidate_record["posted_at"]) >= cutoff:
            recent_candidates.append(candidate_record)
        if reason:
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            if error and reason == "metadata_unavailable":
                errors.append({"stage": "post", "url": candidate["url"], "error": error})
        elif item and len(posts) < args.max_posts:
            posts.append(item)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profiles": profiles,
        "keywords": keywords,
        "days": args.days,
        "candidate_count": len(candidates),
        "recent_candidate_count": len(recent_candidates),
        "matched_post_count": len(posts),
        "skip_reasons": skip_reasons,
        "posts": sorted(posts, key=lambda x: x["posted_at"], reverse=True),
        "recent_candidates": sorted(recent_candidates, key=lambda x: x["posted_at"], reverse=True),
        "errors": errors,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"candidate_count": len(candidates), "recent_candidate_count": len(recent_candidates), "matched_post_count": len(posts), "skip_reasons": skip_reasons, "errors": len(errors)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
