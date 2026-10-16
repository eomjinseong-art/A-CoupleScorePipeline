import json
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reconcile  # noqa: E402
from shorts_format import build_reconcile_lines, contains_english_learning  # noqa: E402

BANNED = ("누가 잘못", "남자 편", "여자 편", "진성", "엄진성", "로빈")


class ReconcileQueueTest(unittest.TestCase):
    def setUp(self):
        self.queue = reconcile.load_queue()

    def test_queue_shape(self):
        ids = [q["id"] for q in self.queue]
        self.assertEqual(len(ids), len(set(ids)))
        for q in self.queue:
            for key in ("id", "speaker", "topic", "hook", "fight", "makeup", "warm", "question"):
                self.assertTrue(q.get(key), f"{q.get('id')} {key}")
            self.assertIn(q["speaker"], ("남자", "여자"))
            self.assertIn("화해", q["question"])

    def test_threads_text_fits_and_has_no_vote(self):
        shop = 0
        for q in self.queue:
            ep = reconcile.to_episode(q)
            text = reconcile.build_threads_text(ep)
            self.assertLessEqual(len(text), 500, q["id"])
            self.assertTrue(text.startswith(q["fight"].split("\n")[0]))
            for word in BANNED:
                self.assertNotIn(word, text, q["id"])
            self.assertFalse(contains_english_learning(text))
            shop += ("avoir24.com" in text)
        self.assertGreater(shop, 0)
        self.assertLessEqual(shop, len(self.queue) // 3 + 1)

    def test_slot_index_is_stable_under_cron_delay(self):
        # 오전 크론(UTC 00:00)이 6시간 늦어도, 저녁 크론(UTC 12:00)이 9시간 늦어도 같은 슬롯.
        d = datetime(2026, 10, 17, 0, 5, tzinfo=timezone.utc)
        self.assertEqual(reconcile.slot_index(d), 2)
        self.assertEqual(reconcile.slot_index(d.replace(hour=6)), 2)
        self.assertEqual(reconcile.slot_index(d.replace(hour=12)), 3)
        self.assertEqual(reconcile.slot_index(d.replace(hour=21, minute=50)), 3)

    def test_queue_falls_back_after_end(self):
        self.assertIsNone(reconcile.next_queue_episode(queue=self.queue, index=len(self.queue)))
        self.assertIsNotNone(reconcile.next_queue_episode(queue=self.queue, index=0))

    def test_shorts_open_with_fight_and_end_with_question(self):
        ep = reconcile.to_episode(self.queue[0])
        lines = build_reconcile_lines(ep)
        self.assertEqual(lines[0].role, "hook")
        self.assertEqual(lines[0].expr, "angry")
        self.assertEqual(lines[-2].role, "warm")
        self.assertEqual(lines[-1].role, "ask")
        self.assertIn("화해", lines[-1].display)
        self.assertEqual(lines[-3].expr in ("happy", "talking"), True)
        title = reconcile.build_title(ep)
        self.assertLessEqual(len(title), 100)
        self.assertTrue(title.startswith(ep["hook"][:10]))


if __name__ == "__main__":
    unittest.main()
