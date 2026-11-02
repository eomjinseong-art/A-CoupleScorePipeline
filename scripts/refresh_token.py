#!/usr/bin/env python3
"""
Threads 장기 토큰을 갱신하고 저장소 시크릿 THREADS_TOKEN 에 다시 쓴다.

Meta: GET https://graph.threads.net/refresh_access_token
      ?grant_type=th_refresh_token&access_token=...
장기 토큰은 발급 24시간이 지난 뒤, 만료 전에만 60일 연장된다.

시크릿을 쓰는 PAT 는 GH_PAT 를 먼저 쓰고, 없거나 저장에 실패하면
SECRETS_WRITE_TOKEN 을 쓴다. 토큰 값은 로그와 이슈에 남기지 않는다.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

from scripts.github_issues import (  # noqa: E402
    THREADS_REFRESH_ISSUE,
    close_issue,
    upsert_issue,
)

REFRESH_URL = "https://graph.threads.net/refresh_access_token"
SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
PAT_NAMES = ("GH_PAT", "SECRETS_WRITE_TOKEN")


class RefreshError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.public_message = message


def _mask(value):
    if value and os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{value}")


def _meta_path():
    override = (os.environ.get("THREADS_TOKEN_META") or "").strip()
    return Path(override) if override else ROOT / "data" / "threads_token.json"


def _alert_path():
    return Path(os.environ.get("THREADS_ALERT_SENT") or "/tmp/threads-token-alert-sent")


def _load_meta():
    path = _meta_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(meta):
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _mark_alert():
    try:
        path = _alert_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("1", encoding="utf-8")
    except Exception:
        pass


def _fail(message):
    print(f"::error::{message}")
    upsert_issue(
        THREADS_REFRESH_ISSUE,
        message + "\n\n토큰 값은 로그와 이슈에 남기지 않습니다. "
        "`THREADS_TOKEN` 과 시크릿 쓰기 PAT(`GH_PAT` 또는 `SECRETS_WRITE_TOKEN`)를 확인해 주세요.\n",
    )
    _mark_alert()
    return 1


def _pats():
    found = []
    for name in PAT_NAMES:
        value = (os.environ.get(name) or "").strip()
        if value:
            _mask(value)
            found.append((name, value))
    return found


def _sheet_token():
    """이번 실행 직전에만 시트에 남겨 둔 최신 토큰. 로그에는 값을 찍지 않는다."""
    creds_json = (
        os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
        or ""
    ).strip()
    if not creds_json:
        return ""
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        ws = gc.open_by_key(SHEET_ID).worksheet("_config")
        for row in ws.get_all_values():
            if len(row) >= 2 and row[0] == "THREADS_TOKEN" and row[1].strip():
                return row[1].strip()
    except Exception:
        print("::warning::시트에 저장된 Threads 토큰은 읽지 못했습니다.")
    return ""


def _backup_sheet(token, expires_iso):
    """시크릿 저장이 실패했을 때만 비공개 시트에 남긴다. 값은 로그에 찍지 않는다."""
    creds_json = (
        os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
        or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
        or ""
    ).strip()
    if not creds_json or not token:
        return False
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        try:
            ws = sh.worksheet("_config")
        except gspread.WorksheetNotFound:
            ws = sh.add_worksheet(title="_config", rows=10, cols=4)
        ws.update("A1:C2", [
            ["key", "value", "expires_at"],
            ["THREADS_TOKEN", token, expires_iso],
        ])
        return True
    except Exception:
        return False


def _candidates():
    env_token = (os.environ.get("THREADS_TOKEN") or "").strip()
    sheet_token = (_sheet_token() or "").strip()
    found = []
    if env_token:
        found.append(("secret", env_token))
    if sheet_token and sheet_token != env_token:
        found.append(("sheet", sheet_token))
    for _source, token in found:
        _mask(token)
    return found


def _too_new(payload):
    err = payload.get("error") if isinstance(payload, dict) else None
    message = ""
    if isinstance(err, dict):
        message = str(err.get("message") or "")
    elif isinstance(err, str):
        message = err
    text = message.lower()
    return any(phrase in text for phrase in ("24 hour", "24 hours", "not eligible", "too new", "not yet"))


def _public_http_error(status, payload):
    err = payload.get("error") if isinstance(payload, dict) else None
    code = ""
    kind = ""
    if isinstance(err, dict):
        raw_code = err.get("code")
        if isinstance(raw_code, int) and not isinstance(raw_code, bool):
            code = str(raw_code)
        raw_type = err.get("type")
        if isinstance(raw_type, str) and raw_type.isascii() and len(raw_type) <= 40:
            kind = raw_type
    detail = f"HTTP {status}"
    if kind:
        detail += f", type={kind}"
    if code:
        detail += f", code={code}"
    return f"Threads 토큰 갱신 API가 실패했습니다 ({detail})."


def _call_refresh(token):
    try:
        resp = requests.get(
            REFRESH_URL,
            params={"grant_type": "th_refresh_token", "access_token": token},
            timeout=30,
        )
    except Exception:
        raise RefreshError("network", "Threads 토큰 갱신 요청이 실패했습니다.")
    payload = None
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if resp.status_code != 200 or not isinstance(payload, dict):
        if isinstance(payload, dict) and _too_new(payload):
            raise RefreshError("too_new", "토큰이 발급 24시간 미만이라 아직 연장할 수 없습니다.")
        raise RefreshError("http", _public_http_error(resp.status_code, payload if isinstance(payload, dict) else {}))
    new_token = payload.get("access_token")
    if not isinstance(new_token, str) or not new_token.strip() or any(ch.isspace() or ord(ch) < 32 for ch in new_token):
        raise RefreshError("empty", "Threads 갱신 응답에 사용할 수 있는 access_token 이 없습니다.")
    new_token = new_token.strip()
    _mask(new_token)
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        expires_in = 60 * 86400
    return new_token, int(expires_in)


def _store_secret(new_token, pats):
    repo = (os.environ.get("GITHUB_REPOSITORY") or "eomjinseong-art/A-CoupleScorePipeline").strip()
    for name, pat in pats:
        env = os.environ.copy()
        env["GH_TOKEN"] = pat
        try:
            res = subprocess.run(
                ["gh", "secret", "set", "THREADS_TOKEN", "--repo", repo],
                input=new_token,
                text=True,
                capture_output=True,
                env=env,
                timeout=45,
            )
        except Exception:
            print(f"::warning::{name} 로 THREADS_TOKEN 저장 요청에 실패했습니다.")
            continue
        if res.returncode == 0:
            return name
        print(f"::warning::{name} 로 THREADS_TOKEN 저장에 실패했습니다.")
    return ""


def _write_expiry(now, expires_in):
    expires_at = (now + timedelta(seconds=expires_in)).strftime("%Y-%m-%dT%H:%M:%SZ")
    days = round(expires_in / 86400, 1)
    _save_meta({
        "refreshed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "expires_at": expires_at,
        "expires_in_days": days,
    })
    return expires_at, days


def _known_expiry():
    meta = _load_meta()
    return meta.get("expires_at") or "unknown"


def refresh_threads_token():
    pats = _pats()
    if not pats:
        return _fail("시크릿을 쓸 PAT가 없습니다. GH_PAT 또는 SECRETS_WRITE_TOKEN 을 등록하세요.")
    print("시크릿 쓰기 후보: " + ", ".join(name for name, _value in pats))

    candidates = _candidates()
    if not candidates:
        return _fail("THREADS_TOKEN 시크릿이 비어 있고, 시트에도 토큰이 없습니다.")

    last_error = "Threads 토큰 갱신에 실패했습니다."
    young = None
    now = datetime.now(timezone.utc)
    for source, token in candidates:
        try:
            new_token, expires_in = _call_refresh(token)
        except RefreshError as exc:
            last_error = exc.public_message
            if exc.kind == "too_new" and young is None:
                young = (source, token)
            print(f"::warning::{source} 토큰 연장 실패: {exc.public_message}")
            continue
        expires_at, days = _write_expiry(now, expires_in)
        stored = _store_secret(new_token, pats)
        if not stored:
            backed = _backup_sheet(new_token, expires_at)
            extra = " 시트 백업은 남겼습니다." if backed else ""
            return _fail("새 토큰을 THREADS_TOKEN 시크릿에 저장하지 못했습니다." + extra)
        print(
            f"THREADS_TOKEN 갱신 완료 ({stored}). "
            f"expires_at={expires_at} expires_in_days={days}"
        )
        close_issue(THREADS_REFRESH_ISSUE, "토큰 자동 갱신이 성공하여 이슈를 닫습니다.")
        return 0

    if young is not None:
        stored = _store_secret(young[1], pats)
        if not stored:
            return _fail("발급 24시간 미만인 토큰을 THREADS_TOKEN 시크릿에 저장하지 못했습니다.")
        expires_at = _known_expiry()
        print(
            "THREADS_TOKEN 시크릿을 현재 장기 토큰으로 저장했습니다 "
            f"({stored}). 갱신 API는 발급 24시간 미만이라 건너뛰었습니다. "
            f"expires_at={expires_at}"
        )
        close_issue(THREADS_REFRESH_ISSUE, "현재 장기 토큰을 시크릿에 저장했습니다. 이슈를 닫습니다.")
        return 0

    return _fail(last_error)


def alert_unhandled_failure():
    if _alert_path().exists():
        print("갱신 실패 이슈는 이미 기록했습니다.")
        return 0
    upsert_issue(
        THREADS_REFRESH_ISSUE,
        "Threads 토큰 갱신 워크플로가 실패했습니다. 로그에서 토큰 값은 지우고 원인만 확인하세요.\n",
    )
    _mark_alert()
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--alert-only":
        sys.exit(alert_unhandled_failure())
    sys.exit(refresh_threads_token())
