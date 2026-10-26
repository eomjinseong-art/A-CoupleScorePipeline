"""
그날의남녀 - 기존 에피소드 일괄 블로그 발행 스크립트
===================================================
1) '블로그용' 시트에 이미 생성되어 있으나 '대기'/'발행실패' 상태인 글을 먼저 발행하고,
2) 남은 한도 내에서 '한국어' 시트의 완료 에피소드 중 '블로그용' 시트에 없는 것들을
   순차적으로 블로그 글 생성 → 시트 저장 → Blogger 발행을 수행합니다.

사용법:
  python batch_blog_publish.py [--limit 10] [--delay 5]
"""
import argparse
import json
import os
import time

from google.oauth2 import service_account
from googleapiclient.discovery import build

from generate_blog_content import generate_blog_post
from publish_blogger import (
    BLOG_SHEET,
    SHEET_ID,
    get_blog_id,
    get_blogger_access_token,
    publish_to_blogger,
)


def get_sheets_service():
    creds_json = os.environ.get("GOOGLE_SHEETS_CREDENTIALS") or os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT 환경변수가 설정되지 않았습니다.")
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_info,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50, help="최대 발행 개수 (기본 50, 0=무제한)")
    parser.add_argument("--delay", type=int, default=10, help="글 간 대기 시간 초 (기본 10)")
    args = parser.parse_args()

    print("=" * 60)
    print("그날의남녀 - 기존 에피소드 일괄 블로그 발행")
    print(f"  최대 개수: {'무제한' if args.limit == 0 else f'{args.limit}개'}")
    print(f"  대기 시간: {args.delay}초")
    print("=" * 60)

    service = get_sheets_service()
    blogger_token = get_blogger_access_token()
    blog_id = get_blog_id(blogger_token)

    # 1. '블로그용' 시트 확인 (대기/발행실패 행 우선 처리)
    try:
        blog_result = service.spreadsheets().values().get(
            spreadsheetId=SHEET_ID, range=f"'{BLOG_SHEET}'!A:G"
        ).execute()
        blog_rows = blog_result.get("values", [])
    except Exception:
        blog_rows = []

    published = 0
    failed = 0
    published_eps = set()
    pending_rows = []

    for row_idx, r in enumerate(blog_rows[1:], start=2):
        while len(r) < 7:
            r.append("")
        ep_id, topic, title, html_content, status, blog_url, summary = r[:7]
        if status == "발행완료" and ep_id:
            published_eps.add(ep_id)
        elif status in ("대기", "발행실패") and title and html_content:
            pending_rows.append((row_idx, ep_id, topic, title, html_content))

    print(f"\n'블로그용' 시트 대기/재시도 대상: {len(pending_rows)}개")

    for row_idx, ep_id, topic, title, html_content in pending_rows:
        if 0 < args.limit <= (published + failed):
            break
        print(f"\n[대기분 발행] {ep_id} ({topic}) - {title}")
        tags = f"커플,연애,그날의남녀,{topic},커플싸움,연애고민,커플밸런스게임"
        url = publish_to_blogger(blogger_token, blog_id, title, html_content, tags)
        if url:
            service.spreadsheets().values().update(
                spreadsheetId=SHEET_ID,
                range=f"'{BLOG_SHEET}'!E{row_idx}:F{row_idx}",
                valueInputOption="RAW",
                body={"values": [["발행완료", url]]},
            ).execute()
            published += 1
            if ep_id:
                published_eps.add(ep_id)
        else:
            failed += 1
        time.sleep(args.delay)

    if 0 < args.limit <= (published + failed):
        print("\n" + "=" * 60)
        print(f"완료: {published}건 성공, {failed}건 실패")
        print("=" * 60)
        return

    # 2. '한국어' 시트에서 완료된 에피소드 읽기
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="'한국어'!A:F"
    ).execute()
    rows = result.get("values", [])

    print(f"\n전체 에피소드: {len(rows) - 1}개, 이미 블로그 발행됨: {len(published_eps)}개")

    targets = []
    for i, r in enumerate(rows[1:], start=2):
        if len(r) >= 5 and r[0] == "완료" and r[1] not in published_eps:
            targets.append({
                "row_num": i,
                "ep": r[1],
                "gender": r[2],
                "topic": r[3],
                "script": r[4],
                "question": r[5] if len(r) > 5 else "",
            })

    remaining_limit = (args.limit - (published + failed)) if args.limit > 0 else 0
    if args.limit > 0:
        targets = targets[:max(0, remaining_limit)]

    print(f"추가 신규 발행 대상: {len(targets)}개\n")

    for idx, ep in enumerate(targets, 1):
        print(f"[{idx}/{len(targets)}] {ep['ep']} ({ep['topic']}) 처리 중...")

        try:
            title, html, tags, summary = generate_blog_post(ep)
            print(f"  글 생성 완료: {title}")

            url = publish_to_blogger(blogger_token, blog_id, title, html, tags)

            status = "발행완료" if url else "대기"
            new_row = [ep["ep"], ep["topic"], title, html, status, url or "", summary]
            service.spreadsheets().values().append(
                spreadsheetId=SHEET_ID,
                range=f"'{BLOG_SHEET}'!A:G",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body={"values": [new_row]},
            ).execute()

            if url:
                published += 1
            else:
                failed += 1

        except Exception as e:
            print(f"  오류: {e}")
            failed += 1

        if idx < len(targets):
            time.sleep(args.delay)

    print("\n" + "=" * 60)
    print(f"완료: {published}건 성공, {failed}건 실패")
    print("=" * 60)


if __name__ == "__main__":
    main()
