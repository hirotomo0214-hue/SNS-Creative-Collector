import unittest

from research_selector import canonical_post_key


class ResearchSelectorCanonicalKeyTest(unittest.TestCase):
    def test_instagram_post_types_share_shortcode_key(self):
        code = "DcibsCkAwdP"
        expected = f"instagram:{code}"
        self.assertEqual(canonical_post_key(f"https://www.instagram.com/p/{code}/"), expected)
        self.assertEqual(canonical_post_key(f"https://www.instagram.com/reel/{code}/"), expected)
        self.assertEqual(canonical_post_key(f"https://www.instagram.com/tv/{code}/?utm_source=test"), expected)

    def test_non_instagram_uses_normalized_exact_url(self):
        self.assertEqual(canonical_post_key("https://example.com/Post/"), "https://example.com/post")


if __name__ == "__main__":
    unittest.main()
