import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


def run_yt_dlp(url: str, out_dir: Path) -> bool:
    template = str(out_dir / "content.%(ext)s")
    cmd = [
        sys.executable,
        "-m",
        "yt_dlp",
        "--no-playlist",
        "--merge-output-format",
        "mp4",
        "-o",
        template,
    ]

    cookies_file = os.environ.get("YT_COOKIES_FILE", "")
    if cookies_file and Path(cookies_file).is_file():
        cmd.extend(["--cookies", cookies_file])

    cmd.append(url)
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        return False
    media = [p for p in out_dir.iterdir() if p.is_file() and p.name.startswith("content.")]
    return len(media) > 0


def x_embed_url(url: str) -> str:
    m = re.search(r"/status/(\d+)", url)
    if not m:
        return url
    return f"https://platform.twitter.com/embed/Tweet.html?id={m.group(1)}&theme=light"


def screenshot(url: str, out_dir: Path) -> None:
    target = out_dir / "screenshot.png"
    host = urlparse(url).netloc.lower()
    capture_url = url
    selectors = []

    if "x.com" in host or "twitter.com" in host:
        capture_url = x_embed_url(url)
        selectors = ["article", ".twitter-tweet-rendered", "body"]
    elif "instagram.com" in host:
        selectors = ["article", "main"]
    elif "tiktok.com" in host:
        selectors = ["main"]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = context.new_page()
        page.goto(capture_url, wait_until="networkidle", timeout=90000)
        page.wait_for_timeout(5000)

        captured = False
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible():
                    box = locator.bounding_box()
                    if box and box["width"] > 100 and box["height"] > 100:
                        locator.screenshot(path=str(target))
                        captured = True
                        break
            except Exception:
                pass

        if not captured:
            page.screenshot(path=str(target), full_page=True)

        browser.close()

    if not target.exists() or target.stat().st_size < 5000:
        raise SystemExit("Screenshot output is too small or missing")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--folder-name", required=True)
    parser.add_argument("--mode", choices=["auto", "video", "image"], default="auto")
    args = parser.parse_args()

    out_dir = Path("output") / args.folder_name
    out_dir.mkdir(parents=True, exist_ok=True)

    got_video = False
    if args.mode in ("auto", "video"):
        got_video = run_yt_dlp(args.url, out_dir)
        if args.mode == "video" and not got_video:
            raise SystemExit("Video download failed")

    if args.mode == "image" or (args.mode == "auto" and not got_video):
        screenshot(args.url, out_dir)

    files = [p.name for p in out_dir.iterdir() if p.is_file()]
    if not files:
        raise SystemExit("No output file created")
    print("Created:", ", ".join(files))


if __name__ == "__main__":
    main()
