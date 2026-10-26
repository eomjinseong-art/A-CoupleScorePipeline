#!/usr/bin/env python3
"""
Threads 장기 토큰(Long-Lived Token) 자동 연장 스크립트
- Meta Threads API: https://graph.threads.net/refresh_access_token
- 만료 전(생성 후 24시간 경과 ~ 60일 이내) 호출 시 60일 연장됨
- 연장된 토큰을:
  1) 현재 프로세스 환경변수(THREADS_TOKEN)에 즉시 반영
  2) GitHub Actions Secret (THREADS_TOKEN) 업데이트 (GH_PAT 시크릿이 주입된 경우)
  3) Google Sheets 비공개 _config 탭에 백업 저장 (차기 실행 시 최신 토큰 자동 로드)
  4) data/threads_token.json 에 갱신 일시/만료 예정일 메타데이터 기록
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

META_PATH = Path(__file__).resolve().parent.parent / "data" / "threads_token.json"
REFRESH_INTERVAL_DAYS = 7  # 7일 이상 경과 시 자동 갱신


def _load_meta() -> dict:
    if META_PATH.exists():
        try:
            return json.loads(META_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_meta(meta: dict) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _get_sheet_stored_token() -> str:
    """Google Sheets 비공개 _config 워크시트에 저장된 최신 THREADS_TOKEN 조회"""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "").strip()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20").strip()
    if not creds_json or not sheet_id:
        return ""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        ws = sh.worksheet("_config")
        rows = ws.get_all_values()
        for row in rows:
            if len(row) >= 2 and row[0] == "THREADS_TOKEN" and row[1].strip():
                return row[1].strip()
    except Exception:
        pass
    return ""


def _save_sheet_stored_token(token: str, expires_iso: str) -> bool:
    """Google Sheets 비공개 _config 워크시트에 최신 THREADS_TOKEN 백업"""
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "").strip()
    sheet_id = os.environ.get("GOOGLE_SHEET_ID", "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20").strip()
    if not creds_json or not sheet_id:
        return False
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
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


def _update_github_secret(new_token: str) -> bool:
    """GH_PAT 환경변수가 있을 경우 GitHub Actions THREADS_TOKEN 시크릿 업데이트"""
    gh_pat = os.environ.get("GH_PAT", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "eomjinseong-art/A-CoupleScorePipeline").strip()
    if not gh_pat:
        return False
    try:
        env = os.environ.copy()
        env["GH_TOKEN"] = gh_pat
        res = subprocess.run(
            ["gh", "secret", "set", "THREADS_TOKEN", "--repo", repo],
            input=new_token.encode("utf-8"),
            env=env,
            capture_output=True,
            timeout=30,
        )
        return res.returncode == 0
    except Exception:
        return False


def _commit_meta_if_ci() -> None:
    """GitHub Actions 실행 중이면 data/threads_token.json 메타데이터 커밋 & 푸시"""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(
            ["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"],
            check=False,
        )
        subprocess.run(["git", "add", str(META_PATH)], check=False)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"])
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore: update threads token refresh metadata [skip ci]"],
                check=False,
            )
            subprocess.run(["git", "push"], check=False)
    except Exception:
        pass


def ensure_fresh_threads_token(force: bool = False) -> str:
    """
    THREADS_TOKEN을 확인하고, 마지막 갱신 후 7일 이상 지났거나 force=True이면
    Meta refresh_access_token 엔드포인트로 60일 연장한다.
    반환값: 유효한 최신 THREADS_TOKEN 문자열
    """
    env_token = os.environ.get("THREADS_TOKEN", "").strip()
    sheet_token = _get_sheet_stored_token()
    current_token = sheet_token or env_token

    if not current_token:
        print("  ⚠️ [Threads 토큰] THREADS_TOKEN이 설정되지 않았습니다.")
        return ""

    if sheet_token and sheet_token != env_token:
        os.environ["THREADS_TOKEN"] = sheet_token
        print(f"::add-mask::{sheet_token}")

    meta = _load_meta()
    now = datetime.now(timezone.utc)

    if not force and meta.get("refreshed_at"):
        try:
            last_refreshed = datetime.fromisoformat(meta["refreshed_at"].replace("Z", "+00:00"))
            elapsed_days = (now - last_refreshed).total_seconds() / 86400
            if elapsed_days < REFRESH_INTERVAL_DAYS:
                return current_token
        except Exception:
            pass

    print("  [Threads 토큰] 장기 토큰 자동 연장 요청 중...")
    try:
        resp = requests.get(
            "https://graph.threads.net/refresh_access_token",
            params={
                "grant_type": "th_refresh_token",
                "access_token": current_token,
            },
            timeout=30,
        )
    except Exception as e:
        print(f"  ⚠️ [Threads 토큰] 갱신 요청 네트워크 오류 (기존 토큰 유지): {e}")
        return current_token

    if resp.status_code != 200:
        print(f"  ⚠️ [Threads 토큰] 갱신 응답 코드 {resp.status_code} (기존 토큰 유지)")
        return current_token

    data = resp.json()
    new_token = data.get("access_token", "").strip()
    expires_in = int(data.get("expires_in", 0) or 0)

    if not new_token:
        print("  ⚠️ [Threads 토큰] 응답에 access_token이 없습니다 (기존 토큰 유지)")
        return current_token

    # GitHub Actions 로그에 토큰이 절대 노출되지 않도록 마스킹
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{new_token}")

    os.environ["THREADS_TOKEN"] = new_token
    days = round(expires_in / 86400, 1) if expires_in else 60.0
    expires_at = (now + timedelta(seconds=expires_in or 5184000)).strftime("%Y-%m-%dT%H:%M:%SZ")
    refreshed_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    _save_sheet_stored_token(new_token, expires_at)
    secret_updated = _update_github_secret(new_token)

    meta_out = {
        "refreshed_at": refreshed_at,
        "expires_at": expires_at,
        "expires_in_days": days,
    }
    _save_meta(meta_out)
    _commit_meta_if_ci()

    secret_msg = " + GitHub Secret 반영" if secret_updated else ""
    print(f"  ✅ [완료] THREADS_TOKEN 갱신 완료 (만료 예정: {expires_at}, 약 {days}일{secret_msg})")
    return new_token


def refresh_token() -> None:
    token = ensure_fresh_threads_token(force=True)
    if not token:
        sys.exit(1)


if __name__ == "__main__":
    refresh_token()
