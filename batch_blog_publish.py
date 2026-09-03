"""
그날의남녀 - 기존 완료 에피소드 일괄 블로그 발행
완료된 에피소드를 모두 읽어 블로그 콘텐츠를 생성하고 Blogger에 발행한다.
"""
import os, json, sys, time, csv, io, requests

# 환경 변수
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BLOGGER_CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID")
BLOGGER_CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET")
BLOGGER_REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN")

SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
BLOGGER_API_BASE = "https://www.googleapis.com/blogger/v3"

BLOG_PROMPT = """당신은 '그날의남녀' 유튜브 채널의 블로그 작가입니다.
남녀 두 사람이 싸운 사연을 바탕으로 티스토리/블로거용 심층 분석 블로그 포스팅을 작성해주세요.

## 사연 정보
- 화자: {gender}
- 주제: {topic}
- 대본: {script}
- 마무리 질문: {question}

## 블로그 포스팅 규칙
1. 제목: SEO 친화적, 호기심 유발 (예: "남자친구가 X를 한 이유 - 심리 분석")
2. 도입부: 독자가 공감할 수 있는 질문이나 시나리오로 시작
3. 상황 묘사: 대본의 내용을 자연스러운 서사로 재구성
4. 심리 분석: "이 상황에서 남자는 ~을 느끼고, 여자는 ~할 수 있어요" 형태로 분석
5. 해결책: 구체적인 소통법이나 관계 조언 제시
6. 마무리: 독자에게 질문 + 댓글 유도
7. 길이: 1200~1800자
8. 톤: 친근하지만 진지한 상담가 톤
9. HTML: <div> 태그로 감싸지 말고 순수 HTML만 사용 (p, h2, h3, strong, em 등)

## 출력 형식 (JSON)
{{"title": "블로그 제목", "html": "<p>본문 HTML...</p>", "tags": ["태그1", "태그2"]}}

반드시 JSON만 출력하세요. 다른 설명이나 마크다운 코드 블록은 포함하지 마세요."""


def get_blogger_token():
    resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": BLOGGER_CLIENT_ID,
        "client_secret": BLOGGER_CLIENT_SECRET,
        "refresh_token": BLOGGER_REFRESH_TOKEN,
        "grant_type": "refresh_token"
    })
    if resp.status_code != 200:
        print(f"  토큰 갱신 실패: {resp.status_code}")
        return None
    return resp.json()["access_token"]


def get_blog_id(token):
    resp = requests.get(f"{BLOGGER_API_BASE}/users/self/blogs",
                        headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        print(f"  블로그 조회 실패: {resp.status_code}")
        return None
    blogs = resp.json().get("items", [])
    if not blogs:
        print("  연결된 블로그 없음")
        return None
    blog = blogs[0]
    print(f"  블로그: {blog.get('name')} ({blog.get('url')})")
    return blog["id"]


def get_existing_posts(token, blog_id):
    """기존 블로그 포스트 목록에서 EP 번호 추출 (중복 방지)"""
    existing = set()
    url = f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts?maxResults=50"
    while url:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code != 200:
            break
        data = resp.json()
        for post in data.get("items", []):
            title = post.get("title", "")
            # EP 번호 추출
            for word in title.split():
                word = word.replace("EP.", "").replace("-", "")
                if word.isdigit():
                    existing.add(word)
        url = data.get("nextPageToken")
        if url:
            url = f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts?maxResults=50&pageToken={url}"
    print(f"이미 발행된 에피소드: {len(existing)}개")
    return existing


def fetch_episodes():
    """Google Sheets에서 완료된 에피소드 읽기"""
    resp = requests.get(SHEET_CSV_URL)
    resp.encoding = 'utf-8'
    reader = list(csv.DictReader(io.StringIO(resp.text)))
    done = [r for r in reader if r.get("Status", "").strip() == "완료"]
    print(f"전체 에피소드: {len(reader)}개, 완료: {len(done)}개")
    return done


def generate_blog_content(episode):
    """ChatGPT로 블로그 콘텐츠 생성"""
    gender = episode.get("화자", "남자")
    topic = episode.get("주제", "")
    script = episode.get("대본", "")
    question = episode.get("마무리 질문", "")

    prompt = BLOG_PROMPT.format(
        gender=gender, topic=topic, script=script, question=question
    )

    resp = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 3000,
            "temperature": 0.7
        }
    )

    if resp.status_code != 200:
        print(f"  ChatGPT 오류: {resp.status_code} {resp.text[:200]}")
        return None

    raw = resp.json()["choices"][0]["message"]["content"].strip()
    raw = raw.removeprefix("```json").removesuffix("```").strip()

    try:
        data = json.loads(raw)
        return data
    except json.JSONDecodeError as e:
        print(f"  JSON 파싱 실패: {e}")
        print(f"  원본: {raw[:200]}")
        return None


def publish_to_blogger(token, blog_id, title, html, tags):
    """Blogger AtomPub API로 발행 (v3 API 대신 사용)"""
    import re
    label_list = [t.strip() for t in tags.split(",") if t.strip()] if isinstance(tags, str) else tags
    categories = "\n".join(
        f"  <category scheme='http://www.blogger.com/atom/ns#' term='{label}'/>"
        for label in label_list
    ) if label_list else ""

    xml_body = f"""<?xml version='1.0' encoding='UTF-8'?>
<entry xmlns='http://www.w3.org/2005/Atom'>
  <title>{title}</title>
{categories}
  <content type='html'><![CDATA[{html}]]></content>
</entry>"""

    for attempt in range(3):
        resp = requests.post(
            f"https://www.blogger.com/feeds/{blog_id}/posts/default",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/atom+xml",
            },
            data=xml_body.encode("utf-8"),
        )

        if resp.status_code in (200, 201):
            link_match = re.search(r'<link[^>]*href="([^"]*)"[^>]*rel="alternate"', resp.text)
            post_url = link_match.group(1) if link_match else ""
            print(f"  발행 완료: {post_url}")
            return post_url
        elif resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  Rate limit (429), {wait}초 대기 후 재시도...")
            time.sleep(wait)
            continue
        else:
            print(f"  발행 실패 ({resp.status_code}): {resp.text[:200]}")
            return None

    print(f"  3회 재시도 실패")
    return None


def main():
    print("=" * 60)
    print("그날의남녀 - 기존 에피소드 일괄 블로그 발행")
    print("=" * 60)

    # 인증 확인
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY 필요"); sys.exit(1)
    if not all([BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN]):
        print("Blogger 인증 정보 필요"); sys.exit(1)

    # Blogger 토큰
    token = get_blogger_token()
    if not token:
        print("Blogger 인증 실패"); sys.exit(1)

    blog_id = get_blog_id(token)
    if not blog_id:
        print("블로그 ID 조회 실패"); sys.exit(1)

    # 에피소드 로드
    episodes = fetch_episodes()
    if not episodes:
        print("완료된 에피소드 없음"); return

    # 기존 발행 에피소드 확인 (중복 방지)
    existing = get_existing_posts(token, blog_id)

    success = 0
    fail = 0
    skip = 0

    for i, ep in enumerate(episodes):
        ep_num = ep.get("EP", "").replace("EP.", "")
        topic = ep.get("주제", "제목 없음")
        gender = ep.get("화자", "남자")

        # 이미 발행된 에피소드 건너뛰기
        if ep_num in existing:
            print(f"\n[{i+1}/{len(episodes)}] EP.{ep_num} - 이미 발행됨, 건너뜀")
            skip += 1
            continue

        print(f"\n[{i+1}/{len(episodes)}] EP.{ep_num} - {topic} ({gender})")

        # 블로그 콘텐츠 생성
        blog_data = generate_blog_content(ep)
        if not blog_data:
            print(f"  콘텐츠 생성 실패, 건너뜀")
            fail += 1
            continue

        title = blog_data.get("title", f"EP.{ep_num} - {topic}")
        html = blog_data.get("html", "")
        tags = blog_data.get("tags", ["그날의남녀", "연애상담", "커플싸움"])

        # 발행
        post_url = publish_to_blogger(token, blog_id, title, html, tags)
        if post_url:
            success += 1
        else:
            fail += 1

        # API rate limit 방지 (Blogger는 분당 30건 제한)
        time.sleep(5)

    print(f"\n{'=' * 60}")
    print(f"완료: {success}건 성공, {fail}건 실패, {skip}건 건너뜀 (총 {len(episodes)}건)")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
