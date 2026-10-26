#!/usr/bin/env python3
"""
Threads 장기 토큰(60일) 자동 연장 스크립트.

만료 전인 유효한 장기 토큰을 th_refresh_token 엔드포인트로 연장하고,
새 토큰을 GitHub Actions Secret (THREADS_TOKEN) 및 설정 저장소에 반영한 뒤
메타데이터를 data/threads_token.json에 기록한다.
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.util
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent.parent
META_FILE = ROOT / "data" / "threads_token.json"
REFRESH_URL = "https://graph.threads.net/refresh_access_token"
SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
CONFIG_SHEET_NAME = "_config"
REFRESH_INTERVAL_DAYS = 7

# GitHub Actions repository public key for eomjinseong-art/A-CoupleScorePipeline
REPO_PUBLIC_KEY_ID = "3380204578043523366"
REPO_PUBLIC_KEY_B64 = "Fq/SG4AAmbsSkT1L2dd/giHlkdsnSnSxSG7er8YyXAM="


def _seal_for_github_secret(secret_value: str) -> dict[str, str] | None:
    """libsodium crypto_box_seal로 GitHub Actions Secret용 암호문을 생성한다."""
    try:
        libname = ctypes.util.find_library("sodium") or "libsodium.so.23"
        sodium = ctypes.CDLL(libname)
        if sodium.sodium_init() < 0:
            return None
        pk = base64.b64decode(REPO_PUBLIC_KEY_B64)
        msg = secret_value.encode("utf-8")
        out = ctypes.create_string_buffer(len(msg) + 48)
        rc = sodium.crypto_box_seal(out, msg, ctypes.c_ulonglong(len(msg)), pk)
        if rc != 0:
            return None
        return {
            "key_id": REPO_PUBLIC_KEY_ID,
            "encrypted_value": base64.b64encode(out.raw).decode("ascii"),
        }
    except Exception:
        return None


def _get_config_worksheet():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS") or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not creds_json:
        return None
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(
            creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(SHEET_ID)
        try:
            return sh.worksheet(CONFIG_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=CONFIG_SHEET_NAME, rows=20, cols=4)
            ws.update(range_name="A1:D1", values=[["key", "value", "refreshed_at", "expires_at"]])
            return ws
    except Exception as e:
        print(f"  [정보] 설정 시트 접근 생략: {e}")
        return None


def _load_token_from_sheet() -> tuple[str | None, str | None]:
    ws = _get_config_worksheet()
    if not ws:
        return None, None
    try:
        rows = ws.get_all_values()
        for row in rows[1:]:
            if len(row) >= 2 and row[0].strip() == "THREADS_TOKEN" and row[1].strip():
                refreshed_at = row[2].strip() if len(row) >= 3 else None
                return row[1].strip(), refreshed_at
    except Exception:
        pass
    return None, None


def _save_token_to_sheet(token: str, refreshed_at: str, expires_at: str) -> None:
    ws = _get_config_worksheet()
    if not ws:
        return
    try:
        rows = ws.get_all_values()
        target_row = None
        for idx, row in enumerate(rows, start=1):
            if idx == 1:
                continue
            if len(row) >= 1 and row[0].strip() == "THREADS_TOKEN":
                target_row = idx
                break
        values = [["THREADS_TOKEN", token, refreshed_at, expires_at]]
        if target_row:
            ws.update(range_name=f"A{target_row}:D{target_row}", values=values)
        else:
            next_row = len(rows) + 1
            ws.update(range_name=f"A{next_row}:D{next_row}", values=values)
    except Exception as e:
        print(f"  [경고] 설정 시트 토큰 저장 실패: {e}")


def _needs_refresh(force: bool = False) -> bool:
    if force or not META_FILE.exists():
        return True
    try:
        meta = json.loads(META_FILE.read_text(encoding="utf-8"))
        refreshed_str = meta.get("refreshed_at", "")
        if not refreshed_str:
            return True
        refreshed_dt = dt.datetime.fromisoformat(refreshed_str.replace("Z", "+00:00"))
        now = dt.datetime.now(dt.timezone.utc)
        return (now - refreshed_dt).total_seconds() >= REFRESH_INTERVAL_DAYS * 86400
    except Exception:
        return True


def _commit_and_push_meta() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    try:
        subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(
            ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
            check=False,
        )
        subprocess.run(["git", "add", str(META_FILE)], check=False)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], check=False)
        if diff.returncode != 0:
            subprocess.run(
                ["git", "commit", "-m", "chore: refresh Threads token metadata [skip ci]"],
                check=False,
            )
            subprocess.run(["git", "push", "origin", "HEAD:main"], check=False)
    except Exception as e:
        print(f"  [경고] 토큰 메타데이터 git push 생략: {e}")


def ensure_fresh_threads_token(force: bool = False) -> str | None:
    """THREADS_TOKEN을 확인하고, 갱신 주기가 도래했으면 Meta API로 60일 연장한다."""
    env_token = (os.environ.get("THREADS_TOKEN") or "").strip()
    sheet_token, _ = _load_token_from_sheet()
    token = sheet_token or env_token
    if not token:
        return None

    if sheet_token:
        print(f"::add-mask::{sheet_token}")
        os.environ["THREADS_TOKEN"] = sheet_token

    if not _needs_refresh(force=force):
        return os.environ.get("THREADS_TOKEN")

    print("  [Threads 토큰] 장기 토큰 자동 연장 요청 중...")
    candidates = []
    if sheet_token:
        candidates.append(sheet_token)
    if env_token and env_token not in candidates:
        candidates.append(env_token)

    data: dict[str, Any] | None = None
    for candidate in candidates:
        try:
            resp = requests.get(
                REFRESH_URL,
                params={"grant_type": "th_refresh_token", "access_token": candidate},
                timeout=30,
            )
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code == 200 and "access_token" in body:
                data = body
                break
            err_msg = body.get("error", {}).get("message", f"HTTP {resp.status_code}")
            print(f"  [경고] 토큰 연장 응답 ({resp.status_code}): {err_msg}")
        except Exception as e:
            print(f"  [경고] 토큰 연장 요청 오류: {e}")

    if not data or "access_token" not in data:
        print("  [경고] Threads 토큰 연장 실패 — 기존 토큰을 유지합니다.")
        return os.environ.get("THREADS_TOKEN")

    new_token = data["access_token"].strip()
    print(f"::add-mask::{new_token}")
    os.environ["THREADS_TOKEN"] = new_token

    expires_in = int(data.get("expires_in", 5184000))
    now = dt.datetime.now(dt.timezone.utc)
    expires_at = now + dt.timedelta(seconds=expires_in)
    refreshed_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    expires_str = expires_at.strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Google Sheet _config 탭에 최신 토큰 저장
    _save_token_to_sheet(new_token, refreshed_str, expires_str)

    # 2. GH_PAT이 있으면 gh secret set 직접 실행
    gh_pat = (os.environ.get("GH_PAT") or os.environ.get("GH_TOKEN") or "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "eomjinseong-art/A-CoupleScorePipeline")
    if gh_pat:
        env = {**os.environ, "GH_TOKEN": gh_pat}
        r = subprocess.run(
            ["gh", "secret", "set", "THREADS_TOKEN", "--repo", repo, "--body", new_token],
            env=env,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            print(f"  ✅ GitHub Secret THREADS_TOKEN 업데이트 완료 ({repo})")

    # 3. data/threads_token.json 메타데이터 및 GitHub Secret용 공개키 봉인 저장
    META_FILE.parent.mkdir(parents=True, exist_ok=True)
    meta_payload: dict[str, Any] = {
        "refreshed_at": refreshed_str,
        "expires_at": expires_str,
        "expires_in_days": round(expires_in / 86400, 1),
    }
    sealed = _seal_for_github_secret(new_token)
    if sealed:
        meta_payload["sealed_for_github_actions"] = sealed

    META_FILE.write_text(json.dumps(meta_payload, indent=2) + "
", encoding="utf-8")
    _commit_and_push_meta()

    print(
        f"  ✅ [완료] THREADS_TOKEN 갱신 완료 "
        f"(만료 예정: {meta_payload['expires_at']}, 약 {meta_payload['expires_in_days']}일)"
    )
    return new_token


def main() -> int:
    token = (os.environ.get("THREADS_TOKEN") or "").strip()
    if not token:
        print("[오류] THREADS_TOKEN 환경변수가 비어 있습니다.", file=sys.stderr)
        return 1
    refreshed = ensure_fresh_threads_token(force=True)
    if not refreshed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
