import argparse
import hashlib
import os
import re
import shutil
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
        "--js-runtimes",
        "node",
        "--remote-components",
        "ejs:github",
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


def instagram_storage_states() -> list[Path]:
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    states = []
    for value in raw.split(":"):
        value = value.strip()
        if value:
            path = Path(value)
            if path.is_file():
                states.append(path)
    return states


def instagram_login_looks_valid(page) -> bool:
    current = page.url.lower()
    if "/accounts/login" in current or "/challenge/" in current:
        return False

    try:
        if page.locator('input[name="username"]').count() > 0:
            return False
    except Exception:
        pass

    return True


def capture_locator_or_page(page, selectors: list[str], target: Path) -> bool:
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

    return target.exists() and target.stat().st_size >= 5000


def capture_page(context, capture_url: str, selectors: list[str], target: Path) -> bool:
    page = context.new_page()
    try:
        page.goto(capture_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        return capture_locator_or_page(page, selectors, target)
    finally:
        page.close()


def instagram_next_locator(page):
    selectors = [
        'button[aria-label="Next"]',
        'button[aria-label="次へ"]',
        '[role="button"]:has(svg[aria-label="Next"])',
        '[role="button"]:has(svg[aria-label="次へ"])',
    ]
    for selector in selectors:
        locator = page.locator(selector).last
        try:
            if locator.count() and locator.is_visible():
                return locator
        except Exception:
            pass
    return None


def capture_instagram_with_context(context, capture_url: str, out_dir: Path, only_if_carousel: bool) -> tuple[bool, bool]:
    page = context.new_page()
    try:
        page.goto(capture_url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        if not instagram_login_looks_valid(page):
            return False, False

        article = page.locator("article").first
        next_button = instagram_next_locator(page)
        is_carousel = next_button is not None
        if only_if_carousel and not is_carousel:
            return True, False

        if not is_carousel:
            target = out_dir / "screenshot.png"
            ok = capture_locator_or_page(page, ["article", "main"], target)
            return ok, False

        hashes = set()
        slide_count = 0
        for _ in range(20):
            target = out_dir / f"slide_{slide_count + 1:02d}.png"
            ok = capture_locator_or_page(page, ["article"], target)
            if not ok:
                if target.exists():
                    target.unlink()
                break

            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest in hashes:
                target.unlink()
                break
            hashes.add(digest)
            slide_count += 1

            next_button = instagram_next_locator(page)
            if next_button is None:
                break
            try:
                next_button.click(timeout=10000)
                page.wait_for_timeout(1200)
            except Exception:
                break

        if slide_count > 0:
            legacy = out_dir / "screenshot.png"
            first_slide = out_dir / "slide_01.png"
            if first_slide.exists():
                shutil.copyfile(first_slide, legacy)
            print(f"Instagram carousel captured: {slide_count} slides")
            return True, True

        return False, True
    finally:
        page.close()


def screenshot(url: str, out_dir: Path, only_if_carousel: bool = False) -> None:
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
        base_kwargs = {
            "viewport": {"width": 1440, "height": 1800},
            "user_agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "locale": "ja-JP",
        }

        try:
            if "instagram.com" in host:
                states = instagram_storage_states()
                for index, state in enumerate(states, start=1):
                    context = browser.new_context(storage_state=str(state), **base_kwargs)
                    try:
                        ok, is_carousel = capture_instagram_with_context(
                            context, capture_url, out_dir, only_if_carousel
                        )
                        if ok:
                            if only_if_carousel and not is_carousel:
                                print(f"Instagram session {index} valid; no carousel capture needed")
                            else:
                                print(f"Instagram session {index} succeeded")
                            return

                        # Only an explicit invalid/login state should advance to the next saved session.
                        probe = context.new_page()
                        try:
                            probe.goto(capture_url, wait_until="domcontentloaded", timeout=90000)
                            probe.wait_for_timeout(2000)
                            if not instagram_login_looks_valid(probe):
                                print(f"Instagram session {index} unavailable; trying next session")
                                continue
                        finally:
                            probe.close()
                        raise SystemExit("Instagram capture failed with a valid session")
                    finally:
                        context.close()

                print("No saved Instagram session succeeded; trying public access")
                context = browser.new_context(**base_kwargs)
                try:
                    ok, is_carousel = capture_instagram_with_context(
                        context, capture_url, out_dir, only_if_carousel
                    )
                    if ok:
                        return
                    raise SystemExit("Instagram screenshot output is too small or missing")
                finally:
                    context.close()

            if only_if_carousel:
                return

            context = browser.new_context(**base_kwargs)
            try:
                if not capture_page(context, capture_url, selectors, target):
                    raise SystemExit("Screenshot output is too small or missing")
            finally:
                context.close()
        finally:
            browser.close()

    if not only_if_carousel and (not target.exists() or target.stat().st_size < 5000):
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

    host = urlparse(args.url).netloc.lower()
    if args.mode == "image" or (args.mode == "auto" and not got_video):
        screenshot(args.url, out_dir)
    elif args.mode == "auto" and got_video and "instagram.com" in host:
        # A mixed Instagram carousel may contain a downloadable video plus static slides.
        screenshot(args.url, out_dir, only_if_carousel=True)

    files = [p.name for p in out_dir.iterdir() if p.is_file()]
    if not files:
        raise SystemExit("No output file created")
    print("Created:", ", ".join(sorted(files)))


if __name__ == "__main__":
    main()
