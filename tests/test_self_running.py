"""워크플로에 유료 AI 키가 없고, 토큰·로그인·글감 알림이 이슈로 남는지 확인한다."""
import io
import json
import os
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import main
import publish_blogger
from batch_blog_publish import already_posted
from scripts.check_queue import LOW_QUEUE_DAYS, check_queue
from scripts.github_issues import GOOGLE_LOGIN_ISSUE, THREADS_REFRESH_ISSUE
from scripts.refresh_token import refresh_threads_token

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeResp:
    def __init__(self, payload, status_code=200):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.headers = {"content-type": "application/json"}

    def json(self):
        return self._payload


class WorkflowContractTests(unittest.TestCase):
    def test_refresh_workflow_is_weekly_and_uses_a_secret_writer(self):
        path = os.path.join(ROOT, ".github", "workflows", "refresh-token.yml")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("workflow_dispatch", text)
        self.assertIn("cron:", text)
        self.assertNotIn("push:", text)
        self.assertIn("secrets.GH_PAT", text)
        self.assertIn("secrets.SECRETS_WRITE_TOKEN", text)
        self.assertIn("secrets.THREADS_TOKEN", text)
        self.assertIn("Threads 토큰 갱신 실패", text)
        with open(os.path.join(ROOT, "main.py"), encoding="utf-8") as main_fh:
            main_src = main_fh.read()
        self.assertNotIn("ensure_fresh_threads_token", main_src)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "ci", "workflows", "refresh-token.yml")))


class QueueAlertTests(unittest.TestCase):
    def test_under_14_days_opens_the_issue(self):
        report = {
            "remaining_days": 13.5,
            "remaining_items": 27,
            "slot": 80,
            "current_id": "R081",
            "total": 104,
        }
        with patch("scripts.check_queue.queue_report", return_value=report), \
                patch("scripts.check_queue.upsert_issue", return_value=True) as upsert, \
                patch("scripts.check_queue.close_issue") as close:
            code = check_queue()
        self.assertEqual(code, 0)
        self.assertEqual(LOW_QUEUE_DAYS, 14)
        self.assertEqual(upsert.call_args.args[0], "글감 부족")
        self.assertIn("13.5", upsert.call_args.args[1])
        close.assert_not_called()

    def test_exactly_14_days_does_not_open_the_issue(self):
        report = {
            "remaining_days": 14,
            "remaining_items": 28,
            "slot": 76,
            "current_id": "R077",
            "total": 104,
        }
        with patch("scripts.check_queue.queue_report", return_value=report), \
                patch("scripts.check_queue.upsert_issue") as upsert, \
                patch("scripts.check_queue.close_issue", return_value=True) as close:
            code = check_queue()
        self.assertEqual(code, 0)
        upsert.assert_not_called()
        close.assert_called_once()
        self.assertEqual(close.call_args.args[0], "글감 부족")


class OAuthAlertTests(unittest.TestCase):
    def test_youtube_invalid_grant_opens_issue(self):
        resp = FakeResp({"error": "invalid_grant", "error_description": "Token has been expired or revoked."}, 400)
        with patch("main.YT_CLIENT_ID", "client-id-value"), \
                patch("main.YT_CLIENT_SECRET", "client-secret-value"), \
                patch("main.YT_REFRESH_TOKEN", "refresh-token-value"), \
                patch("main.requests.post", return_value=resp), \
                patch("scripts.github_issues.upsert_issue", return_value=True) as upsert:
            result = main.upload_youtube("/tmp/does-not-need-to-exist.mp4", "제목", "설명")
        self.assertIsNone(result)
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0], GOOGLE_LOGIN_ISSUE)
        self.assertIn("YouTube", upsert.call_args.args[1])
        self.assertNotIn("refresh-token-value", upsert.call_args.args[1])
        self.assertNotIn("client-secret-value", upsert.call_args.args[1])

    def test_blogger_invalid_grant_opens_issue_and_other_errors_do_not(self):
        denied = FakeResp({"error": "invalid_grant", "error_description": "revoked"}, 400)
        other = FakeResp({"error": "invalid_client"}, 401)
        with patch.dict(os.environ, {
            "BLOGGER_CLIENT_ID": "id",
            "BLOGGER_CLIENT_SECRET": "sec",
            "BLOGGER_REFRESH_TOKEN": "ref",
        }), patch("publish_blogger.requests.post", return_value=denied), \
                patch("scripts.github_issues.upsert_issue", return_value=True) as upsert:
            self.assertIsNone(publish_blogger.get_blogger_token())
        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.args[0], GOOGLE_LOGIN_ISSUE)
        with patch.dict(os.environ, {
            "BLOGGER_CLIENT_ID": "id",
            "BLOGGER_CLIENT_SECRET": "sec",
            "BLOGGER_REFRESH_TOKEN": "ref",
        }), patch("publish_blogger.requests.post", return_value=other), \
                patch("scripts.github_issues.upsert_issue") as upsert:
            self.assertIsNone(publish_blogger.get_blogger_token())
        upsert.assert_not_called()


class RefreshTokenTests(unittest.TestCase):
    def _env(self, **extra):
        base = {
            "THREADS_TOKEN": "",
            "GH_PAT": "",
            "SECRETS_WRITE_TOKEN": "",
            "GITHUB_ACTIONS": "",
            "GITHUB_REPOSITORY": "eomjinseong-art/A-CoupleScorePipeline",
            "GOOGLE_SHEETS_CREDENTIALS": "",
            "GOOGLE_SERVICE_ACCOUNT": "",
        }
        base.update(extra)
        return base

    def test_missing_pat_does_not_call_the_refresh_api(self):
        with patch.dict(os.environ, self._env(THREADS_TOKEN="old-token-value"), clear=False), \
                patch("scripts.refresh_token.requests.get") as get, \
                patch("scripts.refresh_token.upsert_issue", return_value=True) as upsert:
            code = refresh_threads_token()
        self.assertEqual(code, 1)
        get.assert_not_called()
        self.assertEqual(upsert.call_args.args[0], THREADS_REFRESH_ISSUE)
        self.assertNotIn("old-token-value", upsert.call_args.args[1])

    def test_success_writes_the_secret_and_hides_the_token(self):
        old = "th_old_secret_value_abcdefghij"
        new = "th_new_secret_value_klmnopqrst"
        meta = os.path.join(ROOT, "data", ".threads-token-test.json")
        resp = FakeResp({"access_token": new, "expires_in": 5184000})

        def fake_run(cmd, input=None, **kwargs):
            fake_run.calls.append((cmd, input))

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        fake_run.calls = []
        buf = io.StringIO()
        with patch.dict(os.environ, self._env(
            THREADS_TOKEN=old,
            GH_PAT="pat-value-should-stay-hidden",
            SECRETS_WRITE_TOKEN="other-pat",
            THREADS_TOKEN_META=meta,
        ), clear=False), \
                patch("scripts.refresh_token._sheet_token", return_value=""), \
                patch("scripts.refresh_token.requests.get", return_value=resp) as get, \
                patch("scripts.refresh_token.subprocess.run", side_effect=fake_run), \
                patch("scripts.refresh_token.close_issue", return_value=True) as close, \
                patch("scripts.refresh_token.upsert_issue") as upsert, \
                redirect_stdout(buf):
            code = refresh_threads_token()
        self.assertEqual(code, 0)
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["params"]["grant_type"], "th_refresh_token")
        self.assertEqual(get.call_args.kwargs["params"]["access_token"], old)
        secret_calls = [call for call in fake_run.calls if "secret" in call[0]]
        self.assertEqual(len(secret_calls), 1)
        self.assertEqual(secret_calls[0][1], new)
        self.assertIn("THREADS_TOKEN", secret_calls[0][0])
        with open(meta, encoding="utf-8") as fh:
            saved = json.loads(fh.read())
        os.remove(meta)
        self.assertNotIn(new, json.dumps(saved))
        self.assertIn("expires_at", saved)
        expiry = datetime.fromisoformat(saved["expires_at"].replace("Z", "+00:00"))
        self.assertGreater(expiry, datetime.now(timezone.utc) + timedelta(days=50))
        visible = "\n".join(line for line in buf.getvalue().splitlines() if not line.startswith("::add-mask::"))
        self.assertNotIn(old, visible)
        self.assertNotIn(new, visible)
        self.assertNotIn("pat-value-should-stay-hidden", visible)
        self.assertIn("GH_PAT", visible)
        self.assertIn("expires_at=", visible)
        upsert.assert_not_called()
        close.assert_called_once()

    def test_api_failure_opens_the_issue_without_the_token(self):
        old = "th_old_secret_value_abcdefghij"
        resp = FakeResp({"error": {"message": "Invalid OAuth access token.", "type": "OAuthException", "code": 190}}, 400)
        with patch.dict(os.environ, self._env(THREADS_TOKEN=old, GH_PAT="pat"), clear=False), \
                patch("scripts.refresh_token._sheet_token", return_value=""), \
                patch("scripts.refresh_token.requests.get", return_value=resp), \
                patch("scripts.refresh_token.upsert_issue", return_value=True) as upsert:
            code = refresh_threads_token()
        self.assertEqual(code, 1)
        self.assertEqual(upsert.call_args.args[0], THREADS_REFRESH_ISSUE)
        self.assertNotIn(old, upsert.call_args.args[1])
        self.assertNotIn("Invalid OAuth access token", upsert.call_args.args[1])

    def test_too_new_token_is_stored_without_calling_it_a_failure(self):
        current = "th_young_secret_value_zzzzzzzz"
        resp = FakeResp({"error": {"message": "Token is not yet eligible. Wait 24 hours.", "code": 190}}, 400)
        meta = os.path.join(ROOT, "data", ".threads-token-young.json")
        with open(meta, "w", encoding="utf-8") as fh:
            json.dump({"expires_at": "2027-01-01T04:44:30Z", "refreshed_at": "2026-11-02T04:44:30Z"}, fh)

        def fake_run(cmd, input=None, **kwargs):
            fake_run.inputs.append(input)

            class Result:
                returncode = 0
                stdout = ""
                stderr = ""

            return Result()

        fake_run.inputs = []
        with patch.dict(os.environ, self._env(
            THREADS_TOKEN=current,
            SECRETS_WRITE_TOKEN="writer",
            THREADS_TOKEN_META=meta,
        ), clear=False), \
                patch("scripts.refresh_token._sheet_token", return_value=""), \
                patch("scripts.refresh_token.requests.get", return_value=resp), \
                patch("scripts.refresh_token.subprocess.run", side_effect=fake_run), \
                patch("scripts.refresh_token.close_issue", return_value=True), \
                patch("scripts.refresh_token.upsert_issue") as upsert:
            code = refresh_threads_token()
        os.remove(meta)
        self.assertEqual(code, 0)
        self.assertEqual(fake_run.inputs, [current])
        upsert.assert_not_called()


class BatchDedupeTests(unittest.TestCase):
    def test_skips_a_post_that_already_has_the_episode_or_the_hook(self):
        posts = [("답장이 늦었다", "<p>본문</p>"), ("다른 글", "<!-- episode:R009 -->")]
        self.assertTrue(already_posted(posts, "R001", "답장이 늦었다", "처음 줄"))
        self.assertTrue(already_posted(posts, "R009", "새 훅", "다른 싸움 문장입니다"))
        self.assertFalse(already_posted(posts, "R010", "새 훅", "다른 싸움 문장입니다"))


if __name__ == "__main__":
    unittest.main()
