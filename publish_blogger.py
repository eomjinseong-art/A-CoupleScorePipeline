"""
그날의 남녀 - 블로거 자동 발행기
'블로그용' 탭에서 Blog_Status='대기'인 행을 읽어
Google Blogger API로 자동 발행하고 상태를 업데이트한다.
"""
import os, json, sys, time
import requests
import gspread
from google.oauth2.service_account import Credentials

SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
BLOG_SHEET_NAME = "블로그용"
BLOG_HEADERS = ["Blog_Status", "EP", "제목", "HTML", "태그", "Video_URL", "Published_URL", "Published_At"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Blogger API 설정
BLOGGER_SCOPES = ["https://www.googleapis.com/auth/blogger"]
BLOGGER_API_BASE = "https://www.googleapis.com/blogger/v3"


def get_blogger_token():
    """Blogger 전용 OAuth 토큰으로 접근"""
    client_id = os.environ.get("BLOGGER_CLIENT_ID")
    client_secret = os.environ.get("BLOGGER_CLIENT_SECRET")
    refresh_token = os.environ.get("BLOGGER_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        print("  ❌ Blogger OAuth 환경 변수 누락")
        print("  → BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN 필요")
        return None
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"
    })
    if resp.status_code != 200:
        print(f"  ❌ 토큰 갱신 실패: {resp.status_code}")
        return None
    return resp.json()["access_token"]


def get_blog_id(token):
    """내 블로그 ID 조회"""
    resp = requests.get(
        f"{BLOGGER_API_BASE}/users/self/blogs",
        headers={"Authorization": f"Bearer {token}"}
    )
    if resp.status_code != 200:
        print(f"  ❌ 블로그 목록 조회 실패: {resp.status_code} {resp.text[:200]}")
        return None, None
    blogs = resp.json().get("items", [])
    if not blogs:
        print("  ❌ 연결된 블로그가 없습니다.")
        return None, None
    blog = blogs[0]
    blog_id = blog["id"]
    blog_url = blog.get("url", "")
    print(f"  블로그: {blog.get('name', 'unknown')} ({blog_url})")
    return blog_id, blog_url


def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_json:
        raise SystemExit("GOOGLE_SHEETS_CREDENTIALS 필요")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def find_pending_posts(ws):
    """Blog_Status='대기'인 행 찾기"""
    all_vals = ws.get_all_values()
    pending = []
    for i, row in enumerate(all_vals, start=1):
        if i == 1:  # 헤더 스킵
            continue
        if row[0].strip() == "대기" and row[1].strip():
            pending.append((i, row))
    return pending


def update_blog_status(ws, row_num, status, published_url=""):
    """시트 상태 업데이트"""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.update(range_name=f"A{row_num}:H{row_num}", values=[[
        status,                     # Blog_Status
        ws.cell(row_num, 2).value,  # EP
        ws.cell(row_num, 3).value,  # 제목
        ws.cell(row_num, 4).value,  # HTML
        ws.cell(row_num, 5).value,  # 태그
        ws.cell(row_num, 6).value,  # Video_URL
        published_url,              # Published_URL
        now,                        # Published_At
    ]])


def publish_to_blogger(token, blog_id, title, html_content, tags):
    """Blogger API로 포스팅"""
    # 태그를 labels로 변환
    labels = [t.strip() for t in tags.split(",") if t.strip()]

    payload = {
        "kind": "blogger#post",
        "blog": {"id": blog_id},
        "title": title,
        "content": html_content,
        "labels": labels,
    }

    resp = requests.post(
        f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
    )

    if resp.status_code in (200, 201):
        post_data = resp.json()
        post_url = post_data.get("url", "")
        post_id = post_data.get("id", "")
        print(f"  ✅ 발행 완료: {post_url}")
        return post_url
    else:
        print(f"  ❌ 발행 실패 ({resp.status_code}): {resp.text[:300]}")
        if resp.status_code == 401:
            print("  → 토큰 만료. YouTube OAuth 토큰을 다시 생성하세요.")
        elif resp.status_code == 403:
            print("  → Blogger API 권한이 없습니다.")
            print("  → Google Cloud Console에서 Blogger API를 활성화하세요.")
        elif resp.status_code == 400:
            print("  → 요청 데이터가 올바르지 않습니다.")
        return None


def main():
    print("=" * 50)
    print("그날의남녀 - 블로거 자동 발행기")
    print("=" * 50)

    # 토큰 확인
    token = get_blogger_token()
    if not token:
        print("❌ Blogger 인증 실패")
        sys.exit(1)

    # 블로그 ID 조회
    blog_id, blog_url = get_blog_id(token)
    if not blog_id:
        print("❌ 블로그 ID 조회 실패")
        sys.exit(1)

    # 시트 연결
    gc = get_sheets_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(BLOG_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        print(f"❌ '{BLOG_SHEET_NAME}' 탭이 없습니다.")
        print("  → generate_blog_content.py를 먼저 실행하세요.")
        sys.exit(1)

    # 대기 포스팅 찾기
    pending = find_pending_posts(ws)
    if not pending:
        print("⚠ 발행 대기 포스팅 없음. 건너뜀.")
        return

    print(f"발행 대기: {len(pending)}건")

    success_count = 0
    for row_num, row_data in pending:
        ep = row_data[1]
        title = row_data[2]
        html = row_data[3]
        tags = row_data[4]
        video_url = row_data[5]

        print(f"\n--- EP.{ep}: {title} ---")

        # Video_URL 업데이트 (YouTube에서 가져온 경우)
        if video_url and not html:
            print(f"  Video URL: {video_url}")

        # 발행
        post_url = publish_to_blogger(token, blog_id, title, html, tags)
        if post_url:
            update_blog_status(ws, row_num, "발행완료", post_url)
            success_count += 1
        else:
            update_blog_status(ws, row_num, "발행실패")
            print(f"  ❌ EP.{ep} 발행 실패")

        time.sleep(1)  # API rate limit 방지

    print(f"\n{'='*50}")
    print(f"발행 결과: {success_count}/{len(pending)}건 성공")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
