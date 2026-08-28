import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def state_files() -> list[Path]:
    raw = os.environ.get("IG_STORAGE_STATE_FILES", "")
    return [Path(x) for x in raw.split(":") if x and Path(x).is_file()]


def looks_logged_in(page) -> bool:
    current = page.url.lower()
    if "/accounts/login" in current or "/challenge/" in current:
        return False

    try:
        if page.locator('input[name="username"]').count() > 0:
            return False
    except Exception:
        pass

    return True


def main() -> None:
    files = state_files()
    if not files:
        raise SystemExit("No Instagram storageState files were prepared")

    valid = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for index, state in enumerate(files, start=1):
            context = browser.new_context(
                storage_state=str(state),
                locale="ja-JP",
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            try:
                page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(5000)
                ok = looks_logged_in(page)
                print(f"Instagram session {index}: {'VALID' if ok else 'INVALID_OR_CHALLENGED'}")
                if ok:
                    valid += 1
            except Exception as exc:
                print(f"Instagram session {index}: ERROR ({type(exc).__name__})")
            finally:
                page.close()
                context.close()

        browser.close()

    print(f"Valid sessions: {valid}/{len(files)}")
    if valid == 0:
        raise SystemExit("No usable Instagram session found")


if __name__ == "__main__":
    main()
