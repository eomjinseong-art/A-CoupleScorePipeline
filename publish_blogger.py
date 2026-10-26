"""
그날의남녀 - Google Blogger 자동 발행 스크립트
=============================================
Google Sheets '블로그용' 시트에서 '대기' 상태인 글을 읽어
Google Blogger에 자동으로 발행합니다.

Blogger API v3 / AtomPub 인증 방식:
  OAuth2 리프레시 토큰 사용 (YouTube와 동일한 패턴)
  필요 환경변수:
    - BLOGGER_CLIENT_ID
    - BLOGGER_CLIENT_SECRET
    - BLOGGER_REFRESH_TOKEN
    - GOOGLE_SHEETS_CREDENTIALS (또는 GOOGLE_SERVICE_ACCOUNT)
"""
import json
import os
import re
import time
from xml.sax.saxutils import escape, quoteattr

import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build

SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
BLOG_SHEET = "블로그용"

BLOGGER_TOKEN_URI = "https://oauth2.googleapis.com/token"
BLOGGER_API_BASE = "https://www.googleapis.com/blogger/v3"
TARGET_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "2220812024604837629")
TARGET_BLOG_HOST = "couplescore-auto.blogspot.com"


def get_sheets_service():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS") or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not creds_json:
        raise RuntimeError("GOOGLE_SHEETS_CREDENTIALS 환경변수가 설정되지 않았습니다.")
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)


def get_blogger_access_token():
    """ OAuth2 리프레시 토큰으로 액세스 토큰 발급 """
    client_id = os.environ.get("BLOGGER_CLIENT_ID") or os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("BLOGGER_CLIENT_SECRET") or os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("BLOGGER_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError(
            "Blogger OAuth 환경변수가 부족합니다. "
            "BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN을 확인하세요."
        )

    resp = requests.post(
        BLOGGER_TOKEN_URI,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_blog_id(token):
    """ 그날의 남녀 (couplescore-auto.blogspot.com) 블로그 ID 조회 """
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(f"{BLOGGER_API_BASE}/users/self/blogs", headers=headers, timeout=30)
    resp.raise_for_status()
    blogs = resp.json().get("items", [])
    if not blogs:
        raise RuntimeError("연결된 Blogger 블로그가 없습니다. blogger.com에서 블로그를 먼저 생성하세요.")

    blog = None
    for b in blogs:
        if (
            str(b.get("id")) == str(TARGET_BLOG_ID)
            or TARGET_BLOG_HOST in (b.get("url") or "")
            or "그날의 남녀" in (b.get("name") or "")
        ):
            blog = b
            break

    if blog is None:
        print(f"  ⚠️ 목록에서 대상 블로그를 찾지 못해 기본 ID({TARGET_BLOG_ID})를 사용합니다.")
        return str(TARGET_BLOG_ID)

    print(f"  블로그: {blog['name']} ({blog['url']})")
    return blog["id"]


def _extract_atom_url(xml_text):
    m = re.search(
        r"<link[^>]*rel=['\"]alternate['\"][^>]*href=['\"]([^'\"]+)['\"]",
        xml_text or "",
    )
    if not m:
        m = re.search(
            r"<link[^>]*href=['\"]([^'\"]+)['\"][^>]*rel=['\"]alternate['\"]",
            xml_text or "",
        )
    return m.group(1) if m else ""


def publish_to_blogger(token, blog_id, title, html_content, tags):
    """ Blogger에 글 발행 (AtomPub API 우선, REST v3 폴백) """
    raw_labels = [t.strip() for t in (tags or "").split(",") if t.strip()]
    clean_labels = []
    for t in raw_labels:
        cleaned = re.sub(r"[&<>\"'/\\]", " ", t).strip()
        if cleaned and cleaned not in clean_labels:
            clean_labels.append(cleaned)
    labels = clean_labels[:20]

    categories = "\n".join(
        f"  <category scheme='http://www.blogger.com/atom/ns#' term={quoteattr(t)}/>"
        for t in labels
    )
    safe_title = escape(title or "")
    safe_html = (html_content or "").replace("]]>", "]]]]><![CDATA[>")

    xml_body = f"""<?xml version='1.0' encoding='UTF-8'?>
<entry xmlns='http://www.w3.org/2005/Atom'>
  <title type='text'>{safe_title}</title>
{categories}
  <content type='html'><![CDATA[{safe_html}]]></content>
</entry>"""

    atom_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/atom+xml; charset=utf-8",
    }

    resp = None
    for attempt in range(3):
        resp = requests.post(
            f"https://www.blogger.com/feeds/{blog_id}/posts/default",
            headers=atom_headers,
            data=xml_body.encode("utf-8"),
            timeout=60,
        )

        if resp.status_code in (200, 201):
            url = _extract_atom_url(resp.text) or f"https://{TARGET_BLOG_HOST}/"
            print(f"  ✅ 블로그 발행 완료! {url}")
            return url
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  ⏳ 쿼터 초과 (429), {wait}초 대기 후 재시도 ({attempt + 1}/3)...")
            time.sleep(wait)
            continue
        break

    # 폴백: REST v3 API
    v3_headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    v3_body = {
        "kind": "blogger#post",
        "blog": {"id": str(blog_id)},
        "title": title,
        "content": html_content,
        "labels": labels,
    }
    v3_resp = requests.post(
        f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts",
        headers=v3_headers,
        json=v3_body,
        timeout=60,
    )
    if v3_resp.status_code in (200, 201):
        data = v3_resp.json()
        url = data.get("url") or f"https://{TARGET_BLOG_HOST}/"
        print(f"  ✅ 블로그 발행 완료 (v3)! {url}")
        return url

    atom_status = resp.status_code if resp is not None else "N/A"
    atom_err = re.sub(r"[{}\[\]]", "", (resp.text[:250] if resp is not None else "")).strip()
    v3_err = re.sub(r"[{}\[\]]", "", v3_resp.text[:250]).strip()
    print(f"  ❌ 발행 실패 (AtomPub {atom_status}: {atom_err} | v3 {v3_resp.status_code}: {v3_err})")
    return None


def main():
    """ '블로그용' 시트에서 대기 상태인 글을 모두 발행 """
    print("=" * 60)
    print("Blogger 자동 발행 시작")
    print("=" * 60)

    service = get_sheets_service()
    blogger_token = get_blogger_access_token()
    blog_id = get_blog_id(blogger_token)

    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range=f"'{BLOG_SHEET}'!A:G",
    ).execute()
    rows = result.get("values", [])

    if len(rows) <= 1:
        print("발행 대기 중인 글이 없습니다.")
        return

    published_count = 0
    for i, row in enumerate(rows[1:], start=2):
        while len(row) < 7:
            row.append("")

        ep_num, topic, title, html_content, status, blog_url, summary = row[:7]

        if status != "대기":
            continue

        if not title or not html_content:
            print(f"[{ep_num}] 제목 또는 본문이 비어있어 건너뜁니다.")
            continue

        print(f"\n[{ep_num}] '{title}' 발행 중...")

        tags = f"커플,연애,그날의남녀,{topic}"
        url = publish_to_blogger(blogger_token, blog_id, title, html_content, tags)

        if url:
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"'{BLOG_SHEET}'!E{i}:F{i}",
                valueInputOption="RAW",
                body={"values": [["발행완료", url]]},
            ).execute()
            published_count += 1
            print(f"  시트 업데이트 완료 (행 {i})")
            time.sleep(2)
        else:
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"'{BLOG_SHEET}'!E{i}",
                valueInputOption="RAW",
                body={"values": [["발행실패"]]},
            ).execute()

    print(f"\n완료! 총 {published_count}개 글이 발행되었습니다.")


if __name__ == "__main__":
    main()
