"""
그날의 남녀 - 블로그 콘텐츠

예약 파이프라인은 render_blog_post()로 큐 원문을 HTML로 옮긴다. LLM을 호출하지 않는다.
이 파일을 직접 실행할 때만 generate_blog_post()가 Gemini(우선) 또는 OpenAI로 초안을 만든다.
"""
import html
import os, json, sys, csv, io
import requests

from llm_client import chat_completion, completion_text, parse_json_content
from reconcile import SHOP_LINE

SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

BLOG_SHEET_NAME = "블로그용"
BLOG_HEADERS = ["Blog_Status", "EP", "제목", "HTML", "태그", "Video_URL", "Published_URL", "Published_At"]

SYSTEM_PROMPT = """당신은 한국어 블로그 작가입니다. 커플 싸운 이야기를 티스토리/블로거 블로그 포스팅용 HTML로 변환합니다.

규칙:
1. 도입부: 독자가 공감할 수 있는 질문이나 시나리오로 시작
2. 본문: 대본을 바탕으로 상황을 생생하게 묘사
3. 분석: 심리학적 관점에서 "왜 이런 일이 생기는지" 분석 (남자/여자 시각 모두 포함)
4. 조언: 구체적인 해결법이나 건강한 소통법 제시
5. 마무리: 독자에게 질문 + 댓글 유도
6. 톤: 친근하지만 진지한 상담가 톤. "적극적으로~"보다 "때로는~할 수 있어요"처럼 부드럽게
7. 길이: 1200~180자
8. HTML은 <div> 태그로 감싸지 말고, <p>, <strong>, <em>, <h2>, <h3>, <blockquote> 태그만 사용
9. 문단 사이는 빈 줄 1개
10. YouTube 임베드 자리는 <!-- YOUTUBE_EMBED --> 로 표시

출력 형식 (JSON만 출력):
{
  "title": "블로그용 제목 (SEO 친화적, 30자 이내)",
  "html": "HTML 포스팅 본문",
  "tags": ["태그1", "태그2", "태그3", "태그4", "태그5"]
}"""


RECONCILE_SYSTEM_PROMPT = SYSTEM_PROMPT.replace(
    "커플 싸운 이야기를", "커플이 싸웠다가 화해한 이야기('우리 오늘 화해했어요')를"
).replace(
    "4. 조언: 구체적인 해결법이나 건강한 소통법 제시",
    "4. 화해: 사과 한마디·작은 선물·산책·그날 입은 옷처럼 구체적인 화해 장면을 따뜻하게 살리고, 따라 해 볼 만한 화해 팁 제시",
).replace(
    "5. 마무리: 독자에게 질문 + 댓글 유도",
    "5. 마무리: '여러분은 어떻게 화해해요?'처럼 화해 경험을 묻는 질문 + 댓글 유도. 누가 잘못했는지 투표는 하지 않음",
)


def get_blogger_credentials():
    """Blogger API용 Google 인증 (YouTube와 동일한 OAuth 토큰 사용)"""
    client_id = os.environ.get("YT_CLIENT_ID")
    client_secret = os.environ.get("YT_CLIENT_SECRET")
    refresh_token = os.environ.get("YT_REFRESH_TOKEN")
    if not all([client_id, client_secret, refresh_token]):
        return None
    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": client_id, "client_secret": client_secret,
        "refresh_token": refresh_token, "grant_type": "refresh_token"
    })
    if token_resp.status_code != 200:
        from scripts.github_issues import report_oauth_refresh_failure
        report_oauth_refresh_failure("YouTube", token_resp)
        return None
    return token_resp.json()["access_token"]


def load_client():
    import gspread
    from google.oauth2.service_account import Credentials

    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS")
    if not creds_json:
        raise SystemExit("GOOGLE_SHEETS_CREDENTIALS 환경변수가 필요합니다.")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return gspread.authorize(creds)


def fetch_pending_episode():
    """메인 시트에서 Status='대기'인 첫 번째 에피소드"""
    resp = requests.get(SHEET_CSV_URL)
    if resp.status_code != 200:
        print(f"  Sheets 접근 실패: {resp.status_code}")
        return None
    content = resp.content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content))
    for row_idx, row in enumerate(reader, start=2):
        if row.get("Status", "").strip() == "대기":
            return {
                "ep": row.get("EP", "").strip(),
                "gender": "남자" if row.get("화자", "").strip() == "남자" else "여자",
                "topic": row.get("주제", "").strip(),
                "script": row.get("대본", "").strip(),
                "question": row.get("마무리 질문", "").strip(),
                "row_num": row_idx,
            }
    return None


def get_or_create_blog_sheet(gc):
    """'블로그용' 탭 가져오기 또는 생성"""
    import gspread

    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(BLOG_SHEET_NAME)
        print(f"  '{BLOG_SHEET_NAME}' 탭 확인됨")
    except gspread.exceptions.WorksheetNotFound:
        print(f"  '{BLOG_SHEET_NAME}' 탭 생성 중...")
        ws = sh.add_worksheet(title=BLOG_SHEET_NAME, rows=100, cols=len(BLOG_HEADERS))
        ws.update(range_name="A1", values=[BLOG_HEADERS])
        # 헤더 스타일
        ws.format("A1:H1", {
            "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.2},
            "textFormat": {"bold": True, "foregroundColor": {"red": 1, "green": 1, "blue": 1}}
        })
        print(f"  '{BLOG_SHEET_NAME}' 탭 생성 완료")
    return ws


def find_next_row(ws):
    """다음 빈 행 번호"""
    all_vals = ws.get_all_values()
    for i, row in enumerate(all_vals, start=1):
        if not any(cell.strip() for cell in row):
            return i
    return len(all_vals) + 1


def _paragraphs(text):
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            lines.append(f"<p>{html.escape(line)}</p>")
    return "\n".join(lines)


def _blog_title(ep):
    title = " ".join((ep.get("hook") or ep.get("topic") or "그날의 남녀").split())
    if len(title) > 80:
        title = title[:79].rstrip() + "…"
    return title


def _blog_tags(ep):
    raw = ["그날의남녀", "커플화해", "화해썰", "연애심리", ep.get("topic") or ""]
    tags = []
    for tag in raw:
        tag = " ".join(str(tag).replace(",", " ").split())
        if tag and tag not in tags:
            tags.append(tag)
    return tags[:5]


def render_blog_post(ep):
    """큐·시트 원문으로 블로그 HTML을 만든다. 네트워크와 LLM을 쓰지 않는다."""
    parts = [f"<p><strong>{html.escape(_blog_title(ep))}</strong></p>"]
    fight = (ep.get("fight") or "").strip()
    makeup = (ep.get("makeup") or "").strip()
    if fight or makeup:
        if fight:
            parts.append("<h2>싸운 이유</h2>")
            parts.append(_paragraphs(fight))
        if makeup:
            parts.append("<h2>이렇게 화해했어요</h2>")
            parts.append(_paragraphs(makeup))
    else:
        script = (ep.get("script") or "").strip()
        if script:
            parts.append("<h2>사연</h2>")
            parts.append(_paragraphs(script))
    warm = (ep.get("psychology") or ep.get("warm") or "").strip()
    if warm:
        parts.append("<h2>오늘의 화해</h2>")
        parts.append(f"<p><strong>{html.escape(warm)}</strong></p>")
    outfit = (ep.get("outfit") or "").strip()
    if outfit:
        parts.append(f"<p>그날 입은 옷: {html.escape(outfit)}</p>")
    if ep.get("shop"):
        parts.append(f"<p>{html.escape(SHOP_LINE)}</p>")
    question = (ep.get("question") or "여러분은 어떻게 화해해요?").strip()
    parts.append(f"<h2>{html.escape(question)}</h2>")
    parts.append("<p>댓글로 여러분만의 화해법을 알려 주세요.</p>")
    return {"title": _blog_title(ep), "html": "\n".join(parts), "tags": _blog_tags(ep)}


def generate_blog_post(ep):
    """수동 실행 전용. Gemini를 우선하고, 키가 없으면 OpenAI를 쓴다."""
    user_prompt = (
        f"화자: {ep['gender']}\n"
        f"주제: {ep['topic']}\n"
        f"대본:\n{ep['script']}\n\n"
        f"마무리 질문: {ep['question']}\n\n"
        f"위 사연을 블로그 포스팅용 HTML로 변환해주세요."
    )
    system = RECONCILE_SYSTEM_PROMPT if ep.get("source") == "reconcile" else SYSTEM_PROMPT
    print(f"  LLM으로 블로그 초안 생성 (Gemini 우선, EP.{ep.get('ep', '')})")
    resp, spec = chat_completion(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=4000,
        temperature=0.7,
        response_format={"type": "json_object"},
    )
    if resp is None:
        print("  ❌ GEMINI_API_KEY(우선) 또는 OPENAI_API_KEY 없음")
        return None
    if resp.status_code != 200:
        print(f"  ❌ LLM 오류 ({spec['provider']} {spec['model']}): {resp.status_code} {resp.text[:200]}")
        return None
    try:
        data = parse_json_content(completion_text(resp))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        print(f"  ❌ JSON 파싱 실패: {exc}")
        return None

    required = ["title", "html", "tags"]
    for key in required:
        if key not in data or not data[key]:
            print(f"  ❌ '{key}' 필드 누락")
            return None

    print(f"  블로그 콘텐츠 생성 완료: {data['title']}")
    return data


def main():
    print("=" * 50)
    print("그날의남녀 - 블로그 콘텐츠 생성기")
    print("=" * 50)

    ep = fetch_pending_episode()
    if not ep:
        print("⚠ 대기 에피소드 없음. 건너뜀.")
        return

    print(f"대상: EP.{ep['ep']} - {ep['topic']}편")

    # 블로그 콘텐츠 생성
    blog_data = generate_blog_post(ep)
    if not blog_data:
        print("❌ 블로그 콘텐츠 생성 실패")
        sys.exit(1)

    # 시트에 기록
    gc = load_client()
    ws = get_or_create_blog_sheet(gc)
    next_row = find_next_row(ws)

    # EP 번호에서 숫자만 추출
    ep_num = ep["ep"].replace("EP.", "").replace("ep.", "").strip()

    row_data = [
        "대기",                    # Blog_Status
        ep_num,                    # EP
        blog_data["title"],        # 제목
        blog_data["html"],         # HTML
        ",".join(blog_data["tags"]),  # 태그
        "",                        # Video_URL (나중에 채움)
        "",                        # Published_URL (나중에 채움)
        "",                        # Published_At (나중에 채움)
    ]

    ws.update(range_name=f"A{next_row}:H{next_row}", values=[row_data])
    print(f"  시트 기록 완료 (행 {next_row})")
    print(f"  제목: {blog_data['title']}")
    print(f"  태그: {', '.join(blog_data['tags'])}")


if __name__ == "__main__":
    main()
