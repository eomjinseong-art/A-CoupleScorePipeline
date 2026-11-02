"""TTS 순서: edge-tts → gTTS → Google Cloud TTS → OpenAI. 네트워크는 호출하지 않는다."""
import os
import unittest
from unittest.mock import patch

import main
from shorts_format import EDGE_TTS_PITCH, EDGE_TTS_RATE, EDGE_VOICES, OPENAI_TTS_MODEL, OPENAI_VOICES


class TtsOrderTests(unittest.TestCase):
    def test_edge_retries_then_uses_gendered_voice(self):
        seen = []

        def synth(text, voice, rate, pitch, dest):
            seen.append((voice, rate, pitch))
            if len(seen) < 3:
                raise RuntimeError("503")
            with open(dest, "wb") as fh:
                fh.write(b"a" * 180)

        sleeps = []
        with patch("main._edge_synthesize", side_effect=synth), \
                patch("main._write_resampled", return_value=True), \
                patch("main._gen_tts_gtts") as gtts, \
                patch("main._gen_tts_openai") as openai_tts:
            ok = main.gen_tts("안녕하세요", "female", "/tmp/edge-female.mp3", sleep=sleeps.append)
        self.assertTrue(ok)
        self.assertEqual(sleeps, [1, 2])
        self.assertEqual(seen[-1], (EDGE_VOICES["female"], EDGE_TTS_RATE, EDGE_TTS_PITCH["female"]))
        gtts.assert_not_called()
        openai_tts.assert_not_called()

    def test_male_voice_is_injoon(self):
        seen = {}

        def synth(text, voice, rate, pitch, dest):
            seen["voice"] = voice
            with open(dest, "wb") as fh:
                fh.write(b"b" * 180)

        with patch("main._edge_synthesize", side_effect=synth), \
                patch("main._write_resampled", return_value=True):
            ok = main.gen_tts("안녕하세요", "male", "/tmp/edge-male.mp3", sleep=lambda _s: None)
        self.assertTrue(ok)
        self.assertEqual(seen["voice"], EDGE_VOICES["male"])

    def test_edge_backoff_then_gtts_stops_the_chain(self):
        sleeps = []
        with patch("main._edge_synthesize", side_effect=RuntimeError("down")), \
                patch("main._gen_tts_gtts", return_value=True) as gtts, \
                patch("main._gen_tts_google") as google, \
                patch("main._gen_tts_openai") as openai_tts:
            ok = main.gen_tts("안녕", "male", "/tmp/tts-fallback.mp3", sleep=sleeps.append)
        self.assertTrue(ok)
        self.assertEqual(sleeps, [1, 2, 4])
        gtts.assert_called_once()
        google.assert_not_called()
        openai_tts.assert_not_called()

    def test_openai_is_last_and_only_with_a_key(self):
        order = []
        captured = {}

        class Resp:
            status_code = 200
            content = b"mp3-bytes"
            text = ""

        def post(url, **kwargs):
            order.append(url)
            captured.update(kwargs)
            return Resp()

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test", "GCP_TTS_KEY": ""}), \
                patch("main._gen_tts_edge", side_effect=lambda *a, **k: order.append("edge") or False), \
                patch("main._gen_tts_gtts", side_effect=lambda *a, **k: order.append("gtts") or False), \
                patch("main._gen_tts_google", side_effect=lambda *a, **k: order.append("google") or False), \
                patch("main.requests.post", side_effect=post), \
                patch("main._write_resampled", return_value=True):
            ok = main.gen_tts("안녕", "male", "/tmp/tts-openai.mp3", sleep=lambda _s: None)
        self.assertTrue(ok)
        self.assertEqual(order[:3], ["edge", "gtts", "google"])
        self.assertIn("api.openai.com", order[3])
        self.assertEqual(captured["json"]["voice"], OPENAI_VOICES["male"])
        self.assertEqual(captured["json"]["model"], OPENAI_TTS_MODEL)

    def test_openai_skipped_without_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), \
                patch("main._gen_tts_edge", return_value=False), \
                patch("main._gen_tts_gtts", return_value=False), \
                patch("main._gen_tts_google", return_value=False), \
                patch("main.requests.post") as post:
            ok = main.gen_tts("안녕", "female", "/tmp/tts-none.mp3", sleep=lambda _s: None)
        self.assertFalse(ok)
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
