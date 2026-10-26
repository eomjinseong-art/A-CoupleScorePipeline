"""
그날의 남녀 - 블로거 자동 발행기
'블로그용' 탭에서 Blog_Status='대기'인 행을 읽어
Google Blogger API로 자동 발행하고 상태를 업데이트한다.
"""
import os, json, re, sys, time
from xml.sax.saxutils import escape, quoteattr
import requests

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:
    gspread = None
    Credentials = None

SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
BLOG_SHEET_NAME = "블로그용"
BLOG_HEADERS = ["Blog_Status", "EP", "제목", "HTML", "태그", "Video_URL", "Published_URL", "Published_At"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Blogger API 설정
BLOGGER_SCOPES = ["https://www.googleapis.com/auth/blogger"]
BLOGGER_API_BASE = "https://www.googleapis.com/blogger/v3"
TARGET_BLOG_ID = os.environ.get("BLOGGER_BLOG_ID", "2220812024604837629")
TARGET_BLOG_URL_HINT = "couplescore-auto.blogspot.com"
TARGET_BLOG_NAME_HINT = "그날의 남녀"


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


get_blogger_access_token = get_blogger_token


def get_blog_id(token):
    """그날의 남녀 (couplescore-auto.blogspot.com) 블로그 ID 조회"""
    resp = requests.get(
        f"{BLOGGER_API_BASE}/users/self/blogs",
        headers={"Authorization": f"Bearer {token}"}
    )
    if resp.status_code == 200:
        blogs = resp.json().get("items", [])
        for blog in blogs:
            b_id = str(blog.get("id", ""))
            b_url = blog.get("url", "") or ""
            b_name = blog.get("name", "") or ""
            if (
                b_id == TARGET_BLOG_ID
                or TARGET_BLOG_URL_HINT in b_url
                or TARGET_BLOG_NAME_HINT in b_name
                or "그날의남녀" in b_name.replace(" ", "")
            ):
                print(f"  블로그: {b_name} ({b_url})")
                return b_id, b_url

    if TARGET_BLOG_ID:
        blog_url = f"https://{TARGET_BLOG_URL_HINT}/"
        print(f"  블로그(기본값): {TARGET_BLOG_NAME_HINT} ({blog_url})")
        return TARGET_BLOG_ID, blog_url

    print("  ❌ 연결된 대상 블로그를 찾지 못했습니다.")
    return None, None


def get_sheets_client():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS") or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
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
        if i == 1:
            continue
        if len(row) >= 4 and row[0].strip() in ("대기", "발행실패") and row[1].strip():
            pending.append((i, row))
    return pending


def update_blog_status(ws, row_num, status, published_url=""):
    """시트 상태 업데이트"""
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws.update(range_name=f"A{row_num}:H{row_num}", values=[[
        status,
        ws.cell(row_num, 2).value,
        ws.cell(row_num, 3).value,
        ws.cell(row_num, 4).value,
        ws.cell(row_num, 5).value,
        ws.cell(row_num, 6).value,
        published_url,
        now,
    ]])


def publish_to_blogger(token, blog_id, title, html_content, tags):
    """Blogger AtomPub API 우선 발행 (실패 시 v3 REST 폴백)"""
    if isinstance(tags, str):
        labels = [t.strip() for t in tags.split(",") if t.strip()]
    elif isinstance(tags, list):
        labels = [str(t).strip() for t in tags if str(t).strip()]
    else:
        labels = []

    categories = "\n".join(
        f"  <category scheme='http://www.blogger.com/atom/ns#' term={quoteattr(label)}/>"
        for label in labels
    )
    safe_title = escape(title or "")
    safe_html = (html_content or "").replace("]]>", "]]]]><![CDATA[>")
    xml_body = (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<entry xmlns='http://www.w3.org/2005/Atom'>\n"
        f"  <title type='text'>{safe_title}</title>\n"
        f"{categories}\n"
        f"  <content type='html'><![CDATA[{safe_html}]]></content>\n"
        "</entry>"
    )

    for attempt in range(3):
        resp = requests.post(
            f"https://www.blogger.com/feeds/{blog_id}/posts/default",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/atom+xml; charset=utf-8",
            },
            data=xml_body.encode("utf-8"),
            timeout=30,
        )
        if resp.status_code in (200, 201):
            link_match = re.search(
                r"<link[^>]*href=['\"]([^'\"]*)['\"][^>]*rel=['\"]alternate['\"]",
                resp.text,
            )
            if not link_match:
                link_match = re.search(
                    r"<link[^>]*rel=['\"]alternate['\"][^>]*href=['\"]([^'\"]*)['\"]",
                    resp.text,
                )
            post_url = link_match.group(1) if link_match else f"https://{TARGET_BLOG_URL_HINT}/"
            print(f"  ✅ 발행 완료: {post_url}")
            return post_url
        if resp.status_code == 429 and attempt < 2:
            wait = 20 * (attempt + 1)
            print(f"  ⚠️ Rate limit (429), {wait}초 대기 후 재시도...")
            time.sleep(wait)
            continue
        err_clean = resp.text[:300].replace("{", "(").replace("}", ")")
        print(f"  ⚠️ AtomPub 응답 ({resp.status_code}): {err_clean}")
        break

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
        timeout=30,
    )
    if resp.status_code in (200, 201):
        post_data = resp.json()
        post_url = post_data.get("url", f"https://{TARGET_BLOG_URL_HINT}/")
        print(f"  ✅ 발행 완료: {post_url}")
        return post_url

    err_clean = resp.text[:300].replace("{", "(").replace("}", ")")
    print(f"  ❌ 발행 실패 ({resp.status_code}): {err_clean}")
    return None


def main():
    print("=" * 50)
    print("그날의남녀 - 블로거 자동 발행기")
    print("=" * 50)

    token = get_blogger_token()
    if not token:
        print("❌ Blogger 인증 실패")
        sys.exit(1)

    blog_id, blog_url = get_blog_id(token)
    if not blog_id:
        print("❌ 블로그 ID 조회 실패")
        sys.exit(1)

    gc = get_sheets_client()
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(BLOG_SHEET_NAME)
    except gspread.exceptions.WorksheetNotFound:
        print(f"❌ '{BLOG_SHEET_NAME}' 탭이 없습니다.")
        sys.exit(1)

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
        print(f"\n--- EP.{ep}: {title} ---")
        post_url = publish_to_blogger(token, blog_id, title, html, tags)
        if post_url:
            update_blog_status(ws, row_num, "발행완료", post_url)
            success_count += 1
        else:
            update_blog_status(ws, row_num, "발행실패")
            print(f"  ❌ EP.{ep} 발행 실패")
        time.sleep(2)

    print(f"\n{'='*50}")
    print(f"발행 결과: {success_count}/{len(pending)}건 성공")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
