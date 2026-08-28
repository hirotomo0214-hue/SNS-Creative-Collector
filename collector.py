import argparse
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
        url,
    ]
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        return False
    media = [p for p in out_dir.iterdir() if p.is_file() and p.name.startswith("content.")]
    return len(media) > 0


def screenshot(url: str, out_dir: Path) -> None:
    target = out_dir / "screenshot.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1600},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="ja-JP",
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(8000)

        host = urlparse(url).netloc.lower()
        selectors = []
        if "x.com" in host or "twitter.com" in host:
            selectors = ["article"]
        elif "instagram.com" in host:
            selectors = ["article", "main"]
        elif "tiktok.com" in host:
            selectors = ["main"]

        captured = False
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if locator.count() and locator.is_visible():
                    locator.screenshot(path=str(target))
                    captured = True
                    break
            except Exception:
                pass

        if not captured:
            page.screenshot(path=str(target), full_page=True)

        browser.close()


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
