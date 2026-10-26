"""Threads 장기 토큰을 연장하고, 만료됐으면 GitHub 이슈로 알린다.

필요한 시크릿:
  - THREADS_TOKEN        : 현재 Threads 장기 토큰 (60일짜리)
  - GH_PAT               : 시크릿을 덮어쓸 수 있는 Personal Access Token (repo 권한)
  - GITHUB_TOKEN         : 워크플로 기본 토큰 (이슈 알림용)
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
TOKEN_META = ROOT / "data" / "threads_token.json"
GRAPH = "https://graph.threads.net"
KST = timezone(timedelta(hours=9))
ALERT_LABEL = "자동알림"


def _request(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode(errors="ignore")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def gh_api(path, data=None, method=None):
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_PAT")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not tok or not repo:
        return None, None
    headers = {
        "Authorization": f"Bearer {tok}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode() if data is not None else None
    return _request(f"https://api.github.com/repos/{repo}{path}", body, headers, method)


def open_alerts():
    label = urllib.parse.quote(ALERT_LABEL)
    code, res = gh_api(f"/issues?state=open&labels={label}&per_page=50")
    return res if code == 200 and isinstance(res, list) else []


def alert(kind, title, body):
    for iss in open_alerts():
        if f"[{kind}]" in iss.get("title", ""):
            print(f"[알림] 이미 열린 이슈 #{iss['number']} 가 있어 새 알림은 생략합니다.")
            return False
    run = (
        f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
        f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
        f"{os.environ.get('GITHUB_RUN_ID', '')}"
    )
    code, res = gh_api(
        "/issues",
        {
            "title": f"[{kind}] {title}",
            "labels": [ALERT_LABEL],
            "body": (
                f"{body}\n\n실행 로그: {run}\n\n"
                "_자동 생성된 알림입니다. 문제가 해결되면 다음 정상 실행 때 자동으로 닫힙니다._"
            ),
        },
        "POST",
    )
    if code in (200, 201):
        print(f"[알림] 이슈 #{res.get('number')} 생성")
        return True
    print(f"[알림] 이슈 생성 실패 HTTP {code}")
    return True


def close_alerts(kinds, note):
    for iss in open_alerts():
        if any(f"[{k}]" in iss.get("title", "") for k in kinds):
            gh_api(f"/issues/{iss['number']}/comments", {"body": note}, "POST")
            gh_api(f"/issues/{iss['number']}", {"state": "closed"}, "PATCH")
            print(f"[알림] 이슈 #{iss['number']} 닫음")


def check_token(token):
    q = urllib.parse.urlencode({"fields": "id,username", "access_token": token})
    return _request(f"{GRAPH}/v1.0/me?{q}")


def refresh(token):
    q = urllib.parse.urlencode({"grant_type": "th_refresh_token", "access_token": token})
    return _request(f"{GRAPH}/refresh_access_token?{q}")


def save_secret(name, value):
    repo = os.environ.get("GITHUB_REPOSITORY")
    pat = (os.environ.get("GH_PAT") or "").strip()
    if not pat or not repo:
        return False, "GH_PAT 미설정"
    env = {**os.environ, "GH_TOKEN": pat}
    res = subprocess.run(
        ["gh", "secret", "set", name, "--repo", repo],
        input=value,
        text=True,
        capture_output=True,
        env=env,
    )
    return res.returncode == 0, (res.stderr or res.stdout).strip()


def write_meta(expires_in):
    now = datetime.now(KST)
    exp = now + timedelta(seconds=int(expires_in or 5184000))
    meta = {
        "refreshed_at": now.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "expires_at": exp.strftime("%Y-%m-%dT%H:%M:%S+09:00"),
    }
    TOKEN_META.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def main():
    token = (os.environ.get("THREADS_TOKEN") or "").strip()
    if not token:
        alert(
            "토큰",
            "THREADS_TOKEN 이 비어 있습니다",
            "GitHub 저장소 Settings → Secrets and variables → Actions 에 `THREADS_TOKEN` 을 등록해 주세요.",
        )
        sys.exit(1)

    code, me = check_token(token)
    if code != 200 or "id" not in me:
        alert(
            "토큰",
            "Threads 토큰이 만료되었거나 유효하지 않습니다",
            "만료된 토큰은 자동 연장이 불가능합니다. Meta 개발자 콘솔에서 새 장기 토큰을 발급해 "
            "`THREADS_TOKEN` 시크릿을 교체해 주세요.",
        )
        sys.exit(1)
    print(f"[확인] @{me.get('username')} 토큰 정상")

    if not (os.environ.get("GH_PAT") or "").strip():
        print("[안내] GH_PAT 가 없어 토큰 상태만 확인하고 연장은 건너뜁니다.")
        return

    code, data = refresh(token)
    new_token = (data or {}).get("access_token")
    if code != 200 or not new_token:
        alert(
            "토큰",
            "Threads 토큰 자동 연장 실패",
            f"HTTP {code} 응답으로 토큰을 연장하지 못했습니다. 만료 전에 확인해 주세요.",
        )
        sys.exit(1)

    print(f"::add-mask::{new_token}")
    ok, msg = save_secret("THREADS_TOKEN", new_token)
    if not ok:
        alert(
            "토큰",
            "연장된 토큰을 시크릿에 저장하지 못했습니다",
            f"`GH_PAT` 권한(repo 또는 secrets 쓰기)을 확인해 주세요.\n\n오류: `{msg}`",
        )
        sys.exit(1)

    meta = write_meta(data.get("expires_in"))
    print(f"[완료] THREADS_TOKEN 갱신 완료 (만료 예정: {meta['expires_at']})")
    close_alerts(["토큰"], f"토큰 자동 연장 성공 (만료 예정: {meta['expires_at']})")


if __name__ == "__main__":
    main()
