from pathlib import Path

from playwright.sync_api import sync_playwright

OUTPUT = Path("instagram-storage-state.json")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            locale="ja-JP",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)

        print("\nInstagramのログインをブラウザ上で完了してください。")
        print("ホーム画面が表示され、検索メニューが使える状態になったら、このターミナルへ戻ってEnterを押してください。\n")
        input()

        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)
        body = page.locator("body").inner_text(timeout=5000).lower()

        invalid_markers = [
            "別のプロフィールを使用",
            "use another profile",
            "パスワードを忘れた場合",
            "forgot password",
        ]
        if any(marker in body for marker in invalid_markers) or page.locator('input[type="password"]').count() > 0:
            browser.close()
            raise SystemExit("ログイン済みホームを確認できませんでした。Instagramのログインを完了してから再実行してください。")

        context.storage_state(path=str(OUTPUT))
        print(f"保存完了: {OUTPUT.resolve()}")
        print("このJSONの中身をチャットやIssueへ貼らないでください。GitHub Secret登録用にBase64化して使用します。")
        browser.close()


if __name__ == "__main__":
    main()
