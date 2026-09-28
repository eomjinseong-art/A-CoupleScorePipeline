"""업로드 없이 Shorts 포맷, 중복 제거, 자막 안전 영역을 확인한다."""
import os
import unittest

from shorts_format import (
    AUDIO_CONFIGS,
    OVERRIDES_CSV,
    SAFE_BOTTOM,
    SAFE_LEFT,
    SAFE_RIGHT,
    SAFE_TOP,
    STROKE,
    VOICES,
    YOUTUBE_TAGS,
    bbox_inside_safe,
    bright_bbox,
    build_description,
    build_lines,
    build_threads_text,
    build_title,
    contains_english_learning,
    find_font,
    load_overrides,
    load_topics,
    prepare_episode,
    render_card,
    rows_for_append,
    select_topics_to_append,
    validate_generated_episode,
    youtube_upload_enabled,
)
from generate_episodes import SYSTEM_PROMPT

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EP078_SCRIPT = (
    "저희 오늘 또 싸웠습니다. 이번 주말도 영화를 보기로 했습니다. "
    "남자친구는 늘 가던 영화관을 골랐습니다. 주차가 편해서 좋다고 했습니다. "
    "저는 새로운 데이트를 하고 싶었습니다. "
    '"영화관도 맨날 똑같으면 우리 너무 지루하잖아." 라고 했습니다. 그래서 싸웠습니다.'
)


def episode_row(**overrides):
    base = {
        "EP": "EP.078",
        "화자": "여자",
        "주제": "영화관",
        "대본": EP078_SCRIPT,
        "마무리 질문": "주차 편한 익숙한 영화관만 계속 가도 괜찮을까요?",
        "훅": "",
        "심리": "",
        "row_num": 78,
    }
    base.update(overrides)
    return base


class FormatTests(unittest.TestCase):
    def test_overrides_cover_078_to_107(self):
        data = load_overrides()
        self.assertEqual(len(data), 30)
        for n in range(78, 108):
            ep = f"EP.{n:03d}"
            self.assertIn(ep, data)
            self.assertTrue(data[ep]["hook"])
            self.assertFalse(data[ep]["hook"].startswith("저희 오늘"))
            self.assertTrue(data[ep]["poll_male"])
            self.assertTrue(data[ep]["poll_female"])
            self.assertTrue(data[ep]["psychology"])

    def test_hooks_follow_the_sheet_facts(self):
        data = load_overrides()
        self.assertNotIn("10번", data["EP.079"]["hook"])
        self.assertIn("세 번", data["EP.079"]["hook"])
        self.assertNotIn("20만", data["EP.088"]["hook"])
        self.assertIn("5만", data["EP.088"]["hook"])
        self.assertNotIn("3시간", data["EP.097"]["hook"])
        self.assertIn("10분", data["EP.097"]["hook"])
        self.assertNotIn("동료", data["EP.101"]["hook"])
        self.assertIn("배우", data["EP.101"]["hook"])
        self.assertNotIn("향수", data["EP.102"]["hook"])
        self.assertNotIn("60만", data["EP.086"]["hook"])
        self.assertNotIn("30만", data["EP.083"]["hook"])
        self.assertIn("칼", data["EP.093"]["hook"])
        self.assertIn("현금", data["EP.106"]["hook"])

    def test_title_leads_with_conflict_and_ep_is_last(self):
        ep = prepare_episode(episode_row())
        title = build_title(ep["hook"], ep["ep"])
        self.assertTrue(title.startswith("주차 편하다고"))
        self.assertTrue(title.endswith("| 그날의남녀 EP.078"))
        self.assertFalse(title.startswith("저희 오늘 또 싸웠습니다"))
        prefixes = set()
        for item_ep, item in load_overrides().items():
            built = build_title(item["hook"], item_ep)
            prefixes.add(built[:16])
            self.assertTrue(built.endswith(f"| 그날의남녀 {item_ep}"))
        self.assertGreater(len(prefixes), 20)

    def test_description_and_tags_are_couple_psychology(self):
        ep = prepare_episode(episode_row())
        description = build_description(ep)
        self.assertIn("연애심리", description)
        self.assertIn("남자 편:", description)
        self.assertIn("여자 편:", description)
        self.assertFalse(contains_english_learning(description))
        blob = " ".join(YOUTUBE_TAGS).lower()
        self.assertIn("커플싸움", blob)
        self.assertIn("연애심리", blob)
        self.assertFalse(contains_english_learning(blob))
        for banned in ("영어", "english", "shorts"):
            self.assertNotIn(banned, blob)

    def test_threads_keeps_the_original_script(self):
        ep = prepare_episode(episode_row())
        text = build_threads_text(ep)
        self.assertIn(EP078_SCRIPT, text)
        self.assertIn("남자 편", text)
        self.assertIn("여자 편", text)
        self.assertLessEqual(len(text), 500)
        self.assertEqual(ep["script"], EP078_SCRIPT)

    def test_opening_is_the_hook_and_both_voices_speak(self):
        ep = prepare_episode(episode_row())
        self.assertEqual(ep["gender"], "여자")
        self.assertEqual(ep["speaker"], "female")
        lines = build_lines(ep)
        self.assertEqual(lines[0].role, "hook")
        self.assertGreaterEqual(lines[0].min_duration, 2.0)
        self.assertEqual(lines[0].display, ep["hook"])
        voices = {line.voice for line in lines}
        self.assertEqual(voices, {"male", "female"})
        roles = [line.role for line in lines]
        self.assertIn("psych", roles)
        self.assertEqual(roles[-2:], ["poll", "poll"])
        self.assertEqual(lines[-2].voice, "male")
        self.assertEqual(lines[-1].voice, "female")
        body_voices = {line.voice for line in lines if line.role == "body"}
        self.assertIn("male", body_voices)
        self.assertNotIn("저희 오늘 또 싸웠습니다.", [line.spoken for line in lines if line.role == "body"])

    def test_voice_profiles_differ(self):
        self.assertNotEqual(VOICES["male"]["name"], VOICES["female"]["name"])
        self.assertLess(AUDIO_CONFIGS["male"]["pitch"], 0)
        self.assertGreater(AUDIO_CONFIGS["female"]["pitch"], 0)

    def test_sheet_hook_column_wins_without_touching_script(self):
        ep = prepare_episode(episode_row(훅="시트에 적은 훅", 심리="시트 심리"))
        self.assertEqual(ep["hook"], "시트에 적은 훅")
        self.assertEqual(ep["psychology"], "시트 심리")
        self.assertEqual(ep["script"], EP078_SCRIPT)

    def test_subtitles_stay_inside_the_safe_area(self):
        font_path = find_font()
        ep = prepare_episode(episode_row())
        shots = [line for line in build_lines(ep) if line.role in {"hook", "body", "psych", "poll"}]
        long_ep = prepare_episode(episode_row(EP="EP.097", 주제="답장", 대본=EP078_SCRIPT))
        shots.append(build_lines(long_ep)[0])
        hook_size = None
        for shot in shots:
            image = render_card(shot, font_path)
            box = bright_bbox(image)
            self.assertTrue(
                bbox_inside_safe(box),
                f"{shot.role} bbox {box} left safe "
                f"x {SAFE_LEFT}-{SAFE_RIGHT} y {SAFE_TOP}-{SAFE_BOTTOM} stroke {STROKE}",
            )
            if shot.role == "hook" and hook_size is None:
                from shorts_format import layout_card
                font = layout_card(shot, font_path)[0][0]
                hook_size = font.size
        self.assertGreaterEqual(hook_size, 60)

    def test_stick_figures_do_not_enter_the_subtitle_band(self):
        from main import paint_shot

        ep = prepare_episode(episode_row())
        font_path = find_font()
        for shot in build_lines(ep):
            if shot.role not in {"hook", "poll"}:
                continue
            image = paint_shot(shot, font_path)
            pixels = image.convert("RGB").load()
            width = image.size[0]
            for y in range(SAFE_BOTTOM + 1):
                for x in range(width):
                    r, g, b = pixels[x, y]
                    if r <= 40 and g <= 40 and b <= 40:
                        continue
                    self.assertGreaterEqual(x, SAFE_LEFT - STROKE)
                    self.assertLessEqual(x, SAFE_RIGHT + STROKE)
                    self.assertGreaterEqual(y, SAFE_TOP - STROKE)

    def test_youtube_schedule_flag(self):
        self.assertTrue(youtube_upload_enabled({}))
        self.assertTrue(youtube_upload_enabled({"YOUTUBE_UPLOAD": "true"}))
        self.assertFalse(youtube_upload_enabled({"YOUTUBE_UPLOAD": "false"}))
        with open(os.path.join(ROOT, ".github/workflows/upload.yml"), encoding="utf-8") as fh:
            workflow = fh.read()
        self.assertIn("cron: '0 0 * * *'", workflow)
        self.assertIn("cron: '0 12 * * *'", workflow)
        self.assertIn("github.event.schedule == '0 12 * * *'", workflow)
        self.assertIn("YOUTUBE_UPLOAD:", workflow)

    def test_new_topics_append_without_duplicates(self):
        catalog = load_topics()
        self.assertEqual(len(catalog), 20)
        self.assertEqual([item.id for item in catalog], [f"N{n:02d}" for n in range(1, 21)])
        header = ["Status", "EP", "화자", "주제", "대본", "마무리 질문", "", "", ""]
        existing = [header, ["완료", "EP.019", "여자", "정산", "대본", "질문"]]
        rows = rows_for_append(existing)
        self.assertEqual(len(rows), 20)
        self.assertEqual(rows[0][0], "대기")
        self.assertEqual(rows[0][1], "EP.020")
        self.assertTrue(rows[0][4].startswith("저희 오늘 또 싸웠습니다."))
        self.assertTrue(rows[0][4].endswith("그래서 싸웠습니다."))
        self.assertIn("/", rows[0][5])
        self.assertTrue(rows[0][6].startswith("[N01]"))
        self.assertEqual(rows[-1][1], "EP.039")
        again = select_topics_to_append(existing + rows)
        self.assertEqual(again, [])

    def test_exact_topic_collision_is_skipped(self):
        header = ["Status", "EP", "화자", "주제", "대본", "마무리 질문"]
        existing = [header, ["대기", "EP.010", "여자", "1원 정산", "이미 있음", ""]]
        chosen = select_topics_to_append(existing)
        self.assertNotIn("N01", [item.id for item in chosen])
        self.assertEqual(len(chosen), 19)

    def test_refill_prompt_and_validation(self):
        for phrase in ("돈·소비", "온도차", "기념일", "전 애인", "새벽 연락", "리모컨", "남자쪽 주장 / 여자쪽 주장"):
            self.assertIn(phrase, SYSTEM_PROMPT)
        self.assertNotIn("영어 공부", SYSTEM_PROMPT.replace("영어 공부, 영어 회화 소재는 쓰지 않습니다.", ""))
        good = {
            "화자": "여자",
            "주제": "리모컨 주도권",
            "훅": "리모컨은 네 거라고, 채널은 왜 네 마음대로야?",
            "대본": "저희 오늘 또 싸웠습니다. 리모컨을 뺏겼습니다. 그래서 싸웠습니다.",
            "심리": "리모컨은 채널이 아니라 오늘 저녁의 주도권이에요.",
            "마무리_질문": "보던 거 끝까지 / 돌려주는 게 예의",
            "Threads_글감": "리모컨 싸움, 남자 편인가요 여자 편인가요?",
        }
        self.assertEqual(validate_generated_episode(good, ["영화관"]), [])
        self.assertTrue(validate_generated_episode(dict(good, 주제="영화관"), ["영화관"]))
        self.assertTrue(validate_generated_episode(dict(good, 마무리_질문="여러분은 어떠셨나요?"), []))
        self.assertTrue(validate_generated_episode(dict(good, 훅="영어공부 때문에 싸웠다"), []))
        self.assertTrue(os.path.exists(OVERRIDES_CSV))


if __name__ == "__main__":
    unittest.main()
