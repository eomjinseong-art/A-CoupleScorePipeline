"""GitHub 이슈를 열거나 같은 제목의 열린 이슈 본문을 갱신한다.

토큰, 시크릿, 응답 원문은 본문과 로그에 넣지 않는다.
"""
import json
import os
import subprocess

GOOGLE_LOGIN_ISSUE = "구글 로그인 다시 필요"
THREADS_REFRESH_ISSUE = "Threads 토큰 갱신 실패"
LOW_QUEUE_ISSUE = "글감 부족"

_SECRET_ENV = (
    "THREADS_TOKEN",
    "GH_PAT",
    "SECRETS_WRITE_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "BLOGGER_REFRESH_TOKEN",
    "BLOGGER_CLIENT_SECRET",
    "YT_REFRESH_TOKEN",
    "YT_CLIENT_SECRET",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GCP_TTS_KEY",
    "GOOGLE_SHEETS_CREDENTIALS",
    "GOOGLE_SERVICE_ACCOUNT",
)


def scrub(text):
    redacted = text or ""
    for key in _SECRET_ENV:
        value = os.environ.get(key) or ""
        if len(value) >= 6:
            redacted = redacted.replace(value, "***")
    return redacted


def _repo():
    return (os.environ.get("GITHUB_REPOSITORY") or "eomjinseong-art/A-CoupleScorePipeline").strip()


def _token():
    return (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()


def _gh(args):
    token = _token()
    repo = _repo()
    if not token or not repo:
        return None
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    try:
        return subprocess.run(
            ["gh", *args, "--repo", repo],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    except Exception:
        return None


def list_open_issues():
    res = _gh(["issue", "list", "--state", "open", "--limit", "100", "--json", "number,title"])
    if res is None or res.returncode != 0:
        return None
    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _find(title):
    issues = list_open_issues()
    if issues is None:
        return None, False
    for issue in issues:
        if issue.get("title") == title:
            return issue, True
    return None, True


def upsert_issue(title, body):
    """같은 제목의 열린 이슈가 있으면 본문을 바꾸고, 없으면 새로 연다."""
    existing, listed = _find(title)
    if not listed:
        print(f"::warning::GitHub 이슈를 확인하지 못했습니다: {title}")
        return False
    if existing:
        res = _gh(["issue", "edit", str(existing["number"]), "--body", body])
        if res is not None and res.returncode == 0:
            print(f"이슈 #{existing['number']} 본문 업데이트: {title}")
            return True
        print(f"::warning::이슈 본문 업데이트 실패: {title}")
        return False
    res = _gh(["issue", "create", "--title", title, "--body", body])
    if res is not None and res.returncode == 0:
        url = (res.stdout or "").strip().splitlines()
        shown = url[-1] if url else ""
        if shown.startswith("https://"):
            print(f"이슈를 만들었습니다: {shown}")
        else:
            print(f"이슈를 만들었습니다: {title}")
        return True
    print(f"::warning::이슈 생성 실패: {title}")
    return False


def close_issue(title, comment):
    existing, listed = _find(title)
    if not listed:
        print(f"::warning::열린 이슈를 확인하지 못했습니다: {title}")
        return False
    if not existing:
        return True
    res = _gh(["issue", "close", str(existing["number"]), "--comment", comment])
    if res is not None and res.returncode == 0:
        print(f"이슈 #{existing['number']} 닫음: {title}")
        return True
    print(f"::warning::이슈를 닫지 못했습니다: {title}")
    return False


def _oauth_error(resp):
    data = None
    try:
        data = resp.json()
    except Exception:
        data = None
    if isinstance(data, dict):
        return {
            "error": str(data.get("error") or ""),
            "error_description": str(data.get("error_description") or ""),
        }
    text = getattr(resp, "text", "") or ""
    if "invalid_grant" in text:
        return {"error": "invalid_grant", "error_description": ""}
    return {"error": "", "error_description": ""}


def is_invalid_grant(resp):
    err = _oauth_error(resp)
    if err["error"] == "invalid_grant":
        return True
    return "invalid_grant" in err["error_description"]


def notify_google_reauth(service):
    """YouTube 또는 Blogger OAuth invalid_grant. 시크릿 값은 적지 않는다."""
    body = (
        f"{service} OAuth 갱신이 invalid_grant 로 거절되었습니다.\n\n"
        "Refresh token이 만료되었거나 취소되었습니다. Google 계정으로 다시 로그인한 뒤 "
        "아래 시크릿을 교체해 주세요.\n\n"
        "- YouTube: `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`\n"
        "- Blogger: `BLOGGER_CLIENT_ID`, `BLOGGER_CLIENT_SECRET`, `BLOGGER_REFRESH_TOKEN`\n\n"
        f"이번 실패: {service}\n"
    )
    return upsert_issue(GOOGLE_LOGIN_ISSUE, body)


def report_oauth_refresh_failure(service, resp):
    """갱신 실패를 기록하고, invalid_grant 이면 이슈를 연다. 반환값은 invalid_grant 여부."""
    status = getattr(resp, "status_code", 0)
    err = _oauth_error(resp)
    code = err["error"] or "unknown"
    print(f"  ❌ {service} 토큰 갱신 실패 ({status}): {code}")
    if is_invalid_grant(resp):
        print(f"  → {service} 로그인을 다시 해야 합니다. 이슈를 엽니다.")
        notify_google_reauth(service)
        return True
    return False
