"""
그날의남녀 - 커밋된 화해 큐를 블로그 템플릿으로 발행 (수동)

workflow_dispatch 로만 돈다. data/reconcile_episodes.json 을
render_blog_post()로 HTML로 옮긴 뒤 Blogger에 올린다. LLM을 호출하지 않는다.
"""
import os
import sys
import time

import requests

from generate_blog_content import render_blog_post
from publish_blogger import (
    BLOGGER_API_BASE,
    get_blog_id,
    get_blogger_access_token,
    publish_to_blogger,
)
import reconcile

BLOGGER_CLIENT_ID = os.environ.get("BLOGGER_CLIENT_ID")
BLOGGER_CLIENT_SECRET = os.environ.get("BLOGGER_CLIENT_SECRET")
BLOGGER_REFRESH_TOKEN = os.environ.get("BLOGGER_REFRESH_TOKEN")


def generate_blog_content(episode):
    """시트 행 또는 큐 항목을 블로그 템플릿으로 만든다. LLM을 호출하지 않는다."""
    if episode.get("fight") or episode.get("source") == "reconcile":
        return render_blog_post(episode)
    return render_blog_post({
        "hook": episode.get("훅") or episode.get("hook") or episode.get("주제") or episode.get("topic") or "",
        "topic": episode.get("주제") or episode.get("topic") or "",
        "script": episode.get("대본") or episode.get("script") or "",
        "fight": episode.get("fight") or "",
        "makeup": episode.get("makeup") or "",
        "psychology": episode.get("심리") or episode.get("psychology") or episode.get("warm") or "",
        "question": episode.get("마무리 질문") or episode.get("question") or "",
        "outfit": episode.get("outfit") or "",
        "shop": bool(episode.get("shop")),
    })


def already_posted(posts, item_id, hook, fight):
    """이미 같은 회차 또는 같은 제목으로 올라간 글이면 True."""
    marker = f"episode:{item_id}"
    first = ""
    for line in (fight or "").splitlines():
        line = line.strip()
        if line:
            first = line[:80]
            break
    for title, content in posts:
        title = title or ""
        content = content or ""
        if marker in title or marker in content:
            return True
        if hook and title.strip() == hook.strip():
            return True
        if len(first) >= 12 and first in content:
            return True
    return False


def get_existing_posts(token, blog_id):
    """기존 글의 제목과 본문. 토큰은 출력하지 않는다."""
    found = []
    url = (
        f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts"
        "?maxResults=20&fetchBodies=true&fields=nextPageToken,items(title,content)"
    )
    while url:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if resp.status_code != 200:
            print(f"  포스트 목록 조회 상태: {resp.status_code}")
            break
        data = resp.json()
        for post in data.get("items", []):
            found.append((post.get("title") or "", post.get("content") or ""))
        page = data.get("nextPageToken")
        if page:
            url = (
                f"{BLOGGER_API_BASE}/blogs/{blog_id}/posts"
                f"?maxResults=20&fetchBodies=true&pageToken={page}"
                "&fields=nextPageToken,items(title,content)"
            )
        else:
            url = None
    print(f"이미 발행된 글: {len(found)}개")
    return found


def main():
    print("=" * 60)
    print("그날의남녀 - 큐 템플릿 일괄 블로그 발행")
    print("=" * 60)

    if not all([BLOGGER_CLIENT_ID, BLOGGER_CLIENT_SECRET, BLOGGER_REFRESH_TOKEN]):
        print("Blogger 인증 정보 필요")
        return 1

    token = get_blogger_access_token()
    if not token:
        print("Blogger 인증 실패")
        return 1

    blog_id, _blog_url = get_blog_id(token)
    if not blog_id:
        print("블로그 ID 조회 실패")
        return 1

    existing = get_existing_posts(token, blog_id)
    queue = reconcile.load_queue()
    print(f"큐: {len(queue)}편")

    success = 0
    fail = 0
    skip = 0
    for item in queue:
        ep = reconcile.to_episode(item)
        blog = render_blog_post(ep)
        title = blog["title"]
        if already_posted(existing, item["id"], title, item.get("fight") or ""):
            skip += 1
            continue
        html = f"<!-- episode:{item['id']} -->\n{blog['html']}"
        print(f"\n{item['id']} {title}")
        post_url = publish_to_blogger(token, blog_id, title, html, blog["tags"])
        if post_url:
            success += 1
            existing.append((title, html))
        else:
            fail += 1
        time.sleep(5)

    print(f"\n{'=' * 60}")
    print(f"완료: {success}건 성공, {fail}건 실패, {skip}건 건너뜀 (총 {len(queue)}건)")
    print(f"{'=' * 60}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
