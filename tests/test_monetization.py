import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monetization import COUPANG_NOTICE, PARTNER_LINK, comment_text, with_disclosure


class MonetizationTest(unittest.TestCase):
    def test_notice_is_first_line_then_link(self):
        text = with_disclosure("훅\n\n본문")
        lines = text.splitlines()
        self.assertEqual(lines[0], "이 영상은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.")
        self.assertIn(PARTNER_LINK, lines[1])
        self.assertTrue(text.endswith("본문"))

    def test_idempotent(self):
        once = with_disclosure("본문")
        self.assertEqual(with_disclosure(once), once)

    def test_link_is_hub_with_utm_no_car_lease(self):
        self.assertTrue(PARTNER_LINK.startswith("https://tinalinkeom.vercel.app/?utm_source=youtube&utm_medium=shorts&utm_campaign="))
        for bad in ("lease", "리스", "dbsense"):
            self.assertNotIn(bad, PARTNER_LINK.lower())

    def test_comment_matches_description_head(self):
        self.assertTrue(comment_text().startswith(COUPANG_NOTICE))
        self.assertEqual(comment_text(), "\n".join(with_disclosure("x").splitlines()[:2]))


if __name__ == "__main__":
    unittest.main()
