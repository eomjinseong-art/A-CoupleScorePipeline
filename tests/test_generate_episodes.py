"""OpenAI 응답을 흉내 내어 파싱·행 변환만 확인한다. 시트에는 쓰지 않는다."""
import json
import os
import unittest
from unittest.mock import patch

from generate_episodes import (
    DEFAULT_OPENAI_MODEL,
    EPISODE_JSON_SCHEMA,
    generate_episodes,
    parse_generated_payload,
    validate_episode,
    episodes_to_rows,
)
from shorts_format import generated_to_row

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sample_episode(**overrides):
    episode = {
        "화자": "여자",
        "주제": "리모컨 주도권",
        "훅": "리모컨은 네 거라고, 채널은 왜 네 마음대로야?",
        "대본": "저희 오늘 또 싸웠습니다. 리모컨을 뺏겼습니다. 그래서 싸웠습니다.",
        "심리": "리모컨은 채널이 아니라 오늘 저녁의 주도권이에요.",
        "마무리_질문": "보던 거 끝까지 / 돌려주는 게 예의",
        "Threads_글감": "리모컨 싸움, 남자 편인가요 여자 편인가요?",
    }
    episode.update(overrides)
    return episode


class FakeMessage:
    def __init__(self, content, refusal=None):
        self.content = content
        self.refusal = refusal


class FakeResponse:
    def __init__(self, content, refusal=None):
        self.choices = [type("Choice", (), {"message": FakeMessage(content, refusal)})()]


class FakeCompletions:
    def __init__(self, contents):
        self.contents = list(contents)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, contents):
        self.chat = type("Chat", (), {})()
        self.chat.completions = FakeCompletions(contents)

    @property
    def calls(self):
        return self.chat.completions.calls


def keep_valid(episodes, existing_topics):
    kept = []
    seen = list(existing_topics)
    for index, episode in enumerate(episodes, start=1):
        if validate_episode(episode, index, seen):
            continue
        kept.append(episode)
        seen.append((episode.get("주제") or "").strip())
    return kept


class ParseAndFormatTests(unittest.TestCase):
    def test_structured_response_becomes_sheet_rows(self):
        payload = {
            "episodes": [
                sample_episode(),
                sample_episode(주제="영화관 편", 훅="같은 영화관 세 번째, 오늘도 그 자리야?"),
                sample_episode(주제="새벽 통화", 훅="새벽 두 시 통화 47분, 내일 회의는 네 몫이야?"),
                sample_episode(화자="외계인", 주제="우주 정산"),
            ]
        }
        client = FakeClient([FakeResponse(json.dumps(payload, ensure_ascii=False))])
        slept = []
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_MODEL", None)
            episodes = generate_episodes(client, ["영화관"], 4, sleep=slept.append)

        self.assertEqual(slept, [])
        call = client.calls[0]
        self.assertEqual(call["model"], DEFAULT_OPENAI_MODEL)
        self.assertEqual(call["response_format"]["type"], "json_schema")
        schema = call["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertEqual(schema["schema"], EPISODE_JSON_SCHEMA)
        self.assertEqual(
            schema["schema"]["properties"]["episodes"]["items"]["required"],
            ["화자", "주제", "훅", "대본", "심리", "마무리_질문", "Threads_글감"],
        )
        self.assertIn("돈·소비", call["messages"][1]["content"])

        kept = keep_valid(episodes, ["영화관"])
        rows = episodes_to_rows(kept, 108, ["영화관"])
        self.assertEqual([row[1] for row in rows], ["EP.108", "EP.109"])
        self.assertEqual(rows[0][0], "대기")
        self.assertEqual(rows[0][2], "여자")
        self.assertEqual(rows[0][3], "리모컨 주도권")
        self.assertTrue(rows[0][4].startswith("저희 오늘 또 싸웠습니다."))
        self.assertEqual(rows[0][5], "보던 거 끝까지 👈 / 👉 돌려주는 게 예의")
        self.assertEqual(rows[0][7], payload["episodes"][0]["훅"])
        self.assertEqual(rows[0][8], payload["episodes"][0]["심리"])
        self.assertEqual(rows[1][3], "새벽 통화")
        self.assertEqual(rows, [generated_to_row(ep, label) for ep, label in zip(kept, ["EP.108", "EP.109"])])

    def test_fenced_json_array_still_parses(self):
        raw = "```json\n" + json.dumps([sample_episode(주제="기념일")], ensure_ascii=False) + "\n```"
        parsed = parse_generated_payload(raw)
        self.assertEqual(parsed[0]["주제"], "기념일")
        rows = episodes_to_rows(parsed, 20, [])
        self.assertEqual(rows[0][1], "EP.020")
        self.assertEqual(rows[0][3], "기념일")

    def test_retry_uses_backoff_then_parses(self):
        good = FakeResponse(json.dumps({"episodes": [sample_episode(주제="에어컨 온도")]}))
        client = FakeClient([
            RuntimeError("rate limit"),
            FakeResponse("not-json"),
            good,
        ])
        slept = []
        episodes = generate_episodes(client, [], 1, sleep=slept.append)
        self.assertEqual(slept, [1, 2])
        self.assertEqual(len(client.calls), 3)
        self.assertEqual(episodes[0]["주제"], "에어컨 온도")

    def test_model_comes_from_env(self):
        client = FakeClient([FakeResponse(json.dumps({"episodes": [sample_episode()]}))])
        with patch.dict(os.environ, {"OPENAI_MODEL": "gpt-4.1"}):
            generate_episodes(client, [], 1, sleep=lambda _seconds: None)
        self.assertEqual(client.calls[0]["model"], "gpt-4.1")

    def test_refusal_and_bad_shape_are_retried_then_raised(self):
        client = FakeClient([
            FakeResponse("", refusal="cannot comply"),
            FakeResponse(json.dumps({"title": "nope"})),
            ValueError("down"),
            RuntimeError("still down"),
        ])
        slept = []
        with self.assertRaises(RuntimeError):
            generate_episodes(client, [], 1, sleep=slept.append)
        self.assertEqual(slept, [1, 2, 4])
        self.assertEqual(len(client.calls), 4)

    def test_workflow_and_docs_dropped_claude(self):
        with open(os.path.join(ROOT, ".github/workflows/upload.yml"), encoding="utf-8") as fh:
            workflow = fh.read()
        self.assertNotIn("anthropic", workflow)
        self.assertNotIn("ANTHROPIC_API_KEY", workflow)
        self.assertIn("OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}", workflow)
        self.assertIn("continue-on-error: true", workflow)
        refill, upload = workflow.split("- name: Run upload pipeline", 1)
        self.assertIn("continue-on-error: true", refill)
        self.assertIn("python generate_episodes.py", refill)
        self.assertIn("python main.py", upload)

        with open(os.path.join(ROOT, "generate_episodes.py"), encoding="utf-8") as fh:
            source = fh.read()
        lowered = source.lower()
        self.assertNotIn("anthropic", lowered)
        self.assertNotIn("claude", lowered)

        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertNotIn("Claude", readme)
        self.assertIn("OpenAI가 편이 갈리는 주제 30개", readme)

        with open(os.path.join(ROOT, "generate_blog_content.py"), encoding="utf-8") as fh:
            blog = fh.read()
        self.assertNotIn("Claude", blog)


if __name__ == "__main__":
    unittest.main()
