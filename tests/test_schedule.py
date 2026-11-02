"""예약 실행은 큐만 읽고, 비면 건너뛴다. 블로그 HTML은 LLM 없이 만든다."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main
import reconcile
from generate_blog_content import generate_blog_post, render_blog_post
from batch_blog_publish import generate_blog_content


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class ScheduleTests(unittest.TestCase):
    def test_empty_queue_skips_without_sheet_or_network(self):
        buf = io.StringIO()
        with patch("main.reconcile.load_queue", return_value=[]), \
                patch("main.fetch_next_episode") as sheet, \
                patch("main.generate_video") as video, \
                patch("main.requests.post") as post, \
                patch.dict(os.environ, {"RECONCILE_QUEUE": "on"}):
            with redirect_stdout(buf):
                code = main.run(["--dry-run", "--index", "0"])
        self.assertEqual(code, 0)
        sheet.assert_not_called()
        video.assert_not_called()
        post.assert_not_called()
        text = buf.getvalue()
        self.assertIn("큐가 비었습니다", text)
        self.assertIn("OpenAI를 호출하지 않고", text)
        with open(main.__file__, encoding="utf-8") as fh:
            run_source = fh.read().split("def run", 1)[1]
        self.assertNotIn("generate_blog_post", run_source)

    def test_slot_past_the_end_does_not_fall_back_to_the_sheet(self):
        item = {
            "id": "R001", "speaker": "여자", "topic": "답장", "hook": "훅",
            "fight": "싸웠어요.", "makeup": "화해했어요.", "warm": "따뜻해요.",
            "question": "여러분은 어떻게 화해해요?",
        }
        with patch("main.reconcile.load_queue", return_value=[item]), \
                patch("main.fetch_next_episode") as sheet, \
                patch.dict(os.environ, {"RECONCILE_QUEUE": "on"}):
            code = main.run(["--dry-run", "--index", "3"])
        self.assertEqual(code, 0)
        sheet.assert_not_called()

    def test_sheet_fallback_only_when_queue_disabled(self):
        with patch.dict(os.environ, {"RECONCILE_QUEUE": "off"}), \
                patch("main.fetch_next_episode", return_value=None) as sheet, \
                patch("main.fetch_queue_episode") as queue:
            code = main.run(["--dry-run"])
        self.assertEqual(code, 1)
        sheet.assert_called_once()
        queue.assert_not_called()

    def test_blog_html_comes_from_the_queue(self):
        queue = reconcile.load_queue()
        ep = reconcile.to_episode(queue[0])
        data = render_blog_post(ep)
        self.assertIn(ep["hook"], data["title"])
        self.assertIn("싸운 이유", data["html"])
        self.assertIn("이렇게 화해했어요", data["html"])
        self.assertIn(ep["psychology"], data["html"])
        self.assertIn("화해", data["html"])
        self.assertNotIn("남자 편", data["html"])
        shop = reconcile.to_episode(next(item for item in queue if item.get("shop")))
        self.assertIn("avoir24.com", render_blog_post(shop)["html"])

    def _blog_payload(self):
        body = {"title": "제목", "html": "<p>본문</p>", "tags": ["그날의남녀"]}
        return {"choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]}

    def test_manual_blog_prefers_gemini(self):
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured["body"] = kwargs["json"]
            return FakeResp(self._blog_payload())

        episode = {"gender": "여자", "ep": "R001", "topic": "답장", "script": "싸웠어요", "question": "어떻게 화해해요?", "source": "reconcile"}
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gem", "OPENAI_API_KEY": "oa", "GEMINI_MODEL": ""}), \
                patch("llm_client.requests.post", side_effect=post):
            data = generate_blog_post(episode)
            batch = generate_blog_content({"화자": "여자", "주제": "답장", "대본": "싸웠어요", "마무리 질문": "어떻게 화해해요?"})
        self.assertEqual(data["title"], "제목")
        self.assertEqual(batch["title"], "제목")
        self.assertIn("generativelanguage.googleapis.com", captured["url"])
        self.assertEqual(captured["body"]["model"], "gemini-2.5-flash")
        self.assertNotIn("api.openai.com", captured["url"])

    def test_manual_blog_uses_openai_only_without_gemini(self):
        captured = {}

        def post(url, **kwargs):
            captured["url"] = url
            captured["model"] = kwargs["json"]["model"]
            return FakeResp(self._blog_payload())

        with patch.dict(os.environ, {"GEMINI_API_KEY": "", "OPENAI_API_KEY": "oa", "OPENAI_MODEL": ""}), \
                patch("llm_client.requests.post", side_effect=post):
            generate_blog_content({"화자": "남자", "주제": "기념일", "대본": "싸웠습니다", "마무리 질문": "어떻게 화해해요?"})
        self.assertIn("api.openai.com", captured["url"])
        self.assertEqual(captured["model"], "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
