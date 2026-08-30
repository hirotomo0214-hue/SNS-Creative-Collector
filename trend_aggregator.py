import argparse
import json
import math
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse

INSTAGRAM_CODE_RE = re.compile(r"instagram\.com/(?:p|reel|tv)/([^/?#]+)", re.I)
MIN_BASELINE_USABLE_ROWS = 10
MIN_CURRENT_USABLE_ROWS = 10


def parse_json_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if not isinstance(value, str):
        return [str(value)]
    value = value.strip()
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(v) for v in parsed if str(v).strip()]
    except Exception:
        pass
    return [value]


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def canonical_post_key(url):
    if not url:
        return None
    m = INSTAGRAM_CODE_RE.search(url)
    if m:
        return f"instagram:{m.group(1)}"
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "")
        path = parsed.path.rstrip("/")
        return f"{host}{path}" if host else url.strip()
    except Exception:
        return url.strip()


def account_key(row):
    url = row.get("アカウントURL") or ""
    if url:
        try:
            path = urlparse(url).path.strip("/")
            if path:
                return path.split("/")[0].lower()
        except Exception:
            pass
    name = row.get("名前") or ""
    if "@" in name:
        return name.rsplit("@", 1)[-1].strip().lower()
    return name.strip().lower()


def completeness(row):
    return sum(row.get(k) not in (None, "", [], "[]") for k in ["構成案", "具体的な構成案", "訴求方法", "案件ジャンル", "媒体", "投稿形態"])


def normalize(rows):
    dedup = {}
    duplicates = []
    for raw in rows:
        row = dict(raw)
        row["案件_list"] = parse_json_list(row.get("案件"))
        row["訴求方法_list"] = parse_json_list(row.get("訴求方法"))
        row["案件ジャンル_list"] = parse_json_list(row.get("案件ジャンル"))
        row["posted_date"] = parse_date(row.get("posted_at") or row.get("date:投稿日:start") or row.get("投稿日"))
        row["post_key"] = canonical_post_key(row.get("動画URL")) or (row.get("名前") or "")
        row["account_key"] = account_key(row)
        key = row["post_key"]
        if key in dedup:
            previous = dedup[key]
            if completeness(row) > completeness(previous):
                duplicates.append({"post_key": key, "kept": row.get("名前"), "dropped": previous.get("名前")})
                dedup[key] = row
            else:
                duplicates.append({"post_key": key, "kept": previous.get("名前"), "dropped": row.get("名前")})
        else:
            dedup[key] = row
    return list(dedup.values()), duplicates


def signal_tokens(row):
    tokens = []
    if row.get("構成案"):
        tokens.append(("structure", row["構成案"]))
    tokens += [("appeal", v) for v in row.get("訴求方法_list", [])]
    tokens += [("genre", v) for v in row.get("案件ジャンル_list", [])]
    if row.get("投稿形態"):
        tokens.append(("format", row["投稿形態"]))
    return tokens


def aggregate_window(rows):
    stats = defaultdict(lambda: {"posts": 0, "accounts": set(), "projects": set(), "examples": []})
    usable_rows = 0
    for row in rows:
        tokens = signal_tokens(row)
        if not tokens:
            continue
        usable_rows += 1
        for kind, name in tokens:
            s = stats[f"{kind}:{name}"]
            s["posts"] += 1
            if row.get("account_key"):
                s["accounts"].add(row["account_key"])
            for project in row.get("案件_list", []):
                s["projects"].add(project)
            if len(s["examples"]) < 5:
                s["examples"].append({"name": row.get("名前"), "url": row.get("動画URL"), "posted_at": str(row.get("posted_date") or "")})
    return stats, usable_rows


def score_signal(cur, prev, current_days, baseline_days, baseline_sufficient):
    cur_posts = cur.get("posts", 0)
    prev_posts = prev.get("posts", 0)
    cur_accounts = len(cur.get("accounts", set()))
    cur_projects = len(cur.get("projects", set()))
    current_rate = cur_posts / max(current_days, 1)
    baseline_rate = prev_posts / max(baseline_days, 1)
    lift = (current_rate + 0.05) / (baseline_rate + 0.05)
    diversity = min(cur_accounts, 5) / 5
    project_diversity = min(cur_projects, 3) / 3
    volume = math.log1p(cur_posts)
    score = round(volume * 2.0 + math.log2(max(lift, 0.01)) * 1.2 + diversity * 1.5 + project_diversity * 0.5, 3)

    if not baseline_sufficient:
        status = "initial_signal" if cur_posts >= 2 and cur_accounts >= 2 else "watch"
    elif cur_posts >= 3 and cur_accounts >= 3 and cur_projects >= 2 and lift >= 1.3:
        status = "trend_candidate"
    elif cur_posts >= 3 and cur_accounts >= 3 and cur_projects == 1:
        status = "campaign_pattern"
    elif cur_posts >= 2 and cur_accounts >= 2:
        status = "emerging"
    else:
        status = "watch"
    concentration = "cross_project" if cur_projects >= 2 else "campaign_concentrated"
    return score, round(lift, 2), status, concentration


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", default="trend_report.json")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--current-days", type=int, default=7)
    ap.add_argument("--baseline-days", type=int, default=28)
    ap.add_argument("--min-posts", type=int, default=2)
    args = ap.parse_args()

    raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
    rows = raw.get("results", []) if isinstance(raw, dict) else raw
    normalized, duplicates = normalize(rows)
    as_of = datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else datetime.now(timezone.utc).date()
    current_start = as_of - timedelta(days=args.current_days - 1)
    baseline_end = current_start - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=args.baseline_days - 1)
    current_rows = [r for r in normalized if r.get("posted_date") and current_start <= r["posted_date"] <= as_of]
    baseline_rows = [r for r in normalized if r.get("posted_date") and baseline_start <= r["posted_date"] <= baseline_end]
    current_stats, current_usable = aggregate_window(current_rows)
    baseline_stats, baseline_usable = aggregate_window(baseline_rows)
    baseline_sufficient = baseline_usable >= MIN_BASELINE_USABLE_ROWS
    current_sufficient = current_usable >= MIN_CURRENT_USABLE_ROWS

    signals = []
    for key, cur in current_stats.items():
        if cur["posts"] < args.min_posts:
            continue
        prev = baseline_stats.get(key, {"posts": 0, "accounts": set(), "projects": set(), "examples": []})
        score, lift, status, concentration = score_signal(cur, prev, args.current_days, args.baseline_days, baseline_sufficient)
        kind, name = key.split(":", 1)
        signals.append({"kind": kind, "name": name, "status": status, "trend_score": score, "current_posts": cur["posts"], "current_accounts": len(cur["accounts"]), "current_projects": len(cur["projects"]), "baseline_posts": prev["posts"], "rate_lift": lift if baseline_sufficient else None, "concentration": concentration, "examples": cur["examples"]})
    signals.sort(key=lambda x: (x["trend_score"], x["current_posts"], x["current_accounts"]), reverse=True)

    missing = [{"name": r.get("名前"), "url": r.get("動画URL"), "posted_at": str(r.get("posted_date") or "")} for r in current_rows if not r.get("構成案") and not r.get("訴求方法_list")]
    confidence = "high" if baseline_sufficient and current_sufficient else "medium" if current_sufficient else "low"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "as_of": str(as_of),
        "windows": {"current": {"start": str(current_start), "end": str(as_of), "days": args.current_days}, "baseline": {"start": str(baseline_start), "end": str(baseline_end), "days": args.baseline_days}},
        "data_quality": {"current_sufficient": current_sufficient, "baseline_sufficient": baseline_sufficient, "confidence": confidence, "interpretation": "trend_comparison" if baseline_sufficient else "initial_signal_only"},
        "input_rows": len(rows), "normalized_unique_rows": len(normalized), "duplicate_rows_removed": len(duplicates), "duplicates": duplicates,
        "current_rows": len(current_rows), "current_usable_rows": current_usable, "baseline_rows": len(baseline_rows), "baseline_usable_rows": baseline_usable,
        "missing_classification_count": len(missing), "missing_classification": missing, "signals": signals,
        "top_structures": [s for s in signals if s["kind"] == "structure"][:10], "top_appeals": [s for s in signals if s["kind"] == "appeal"][:10],
        "top_formats": [s for s in signals if s["kind"] == "format"][:10], "top_genres": [s for s in signals if s["kind"] == "genre"][:10],
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Input rows: {len(rows)}")
    print(f"Unique rows: {len(normalized)}")
    print(f"Duplicates removed: {len(duplicates)}")
    print(f"Current usable rows: {current_usable}/{len(current_rows)}")
    print(f"Baseline usable rows: {baseline_usable}; sufficient={baseline_sufficient}")
    print(f"Interpretation: {payload['data_quality']['interpretation']}")
    print(f"Signals: {len(signals)}")
    for s in signals[:10]:
        print(f"- {s['status']} {s['kind']}:{s['name']} posts={s['current_posts']} accounts={s['current_accounts']} projects={s['current_projects']} lift={s['rate_lift']}")


if __name__ == "__main__":
    main()
