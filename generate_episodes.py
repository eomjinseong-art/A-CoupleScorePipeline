"""
그날의 남녀 - 에피소드 자동 보충 스크립트

Google Sheets에서 "대기" 상태의 에피소드가 특정 임계값 이하로 떨어지면,
OpenAI API를 사용하여 새 에피소드 30개를 자동으로 생성하여 시트에 추가한다.

필요 환경변수:
  OPENAI_API_KEY
  OPENAI_MODEL (기본값 gpt-4.1-mini)
  GOOGLE_SERVICE_ACCOUNT

필요 패키지:
  pip install openai google-auth google-api-python-client requests
"""

import os
import json
import csv
import io
import sys
import time

import requests

from shorts_format import (
    REFILL_SYSTEM_PROMPT,
    generated_to_row,
    rows_for_append,
    validate_generated_episode,
)

# === 설정 ===
SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# "대기" 에피소드가 이 값 이하로 떨어지면 보충 시작
REFILL_THRESHOLD = int(os.environ.get("REFILL_THRESHOLD", "30"))
# 한 번에 생성할 에피소드 수
BATCH_SIZE = int(os.environ.get("REFILL_BATCH_SIZE", "30"))

SYSTEM_PROMPT = REFILL_SYSTEM_PROMPT

DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
# 첫 실패 후 1초, 2초, 4초. 네 번째 실패에서 예외를 올린다.
OPENAI_RETRY_BACKOFF_SECONDS = (1, 2, 4)

EPISODE_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["episodes"],
    "properties": {
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["화자", "주제", "훅", "대본", "심리", "마무리_질문", "Threads_글감"],
                "properties": {
                    "화자": {"type": "string", "enum": ["남자", "여자"]},
                    "주제": {"type": "string"},
                    "훅": {"type": "string"},
                    "대본": {"type": "string"},
                    "심리": {"type": "string"},
                    "마무리_질문": {"type": "string"},
                    "Threads_글감": {"type": "string"},
                },
            },
        }
    },
}


def openai_model():
    return (os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL


def parse_generated_payload(raw):
    """모델 응답을 에피소드 배열로 맞춘다.

    구조화 출력은 {"episodes": [...]} 이고,
    JSON 배열만 오는 응답도 같은 항목 스키마로 받는다.
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```").strip()
        text = text.removesuffix("```").strip()
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("episodes"), list):
        data = data["episodes"]
    if not isinstance(data, list):
        raise ValueError(f"예상치 못한 응답 형식: {type(data)}")
    return data


def load_sheets_service():
    """Google Sheets API 서비스 생성"""
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not creds_json:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT 환경변수가 필요합니다.")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def fetch_public_rows():
    resp = requests.get(SHEET_CSV_URL, timeout=30)
    resp.raise_for_status()
    content = resp.content.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(content)))


def get_pending_count(service):
    """시트에서 "대기" 상태의 에피소드 수를 반환"""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="A:B"  # Status, EP 컬럼만 읽기
    ).execute()
    values = result.get("values", [])
    if len(values) <= 1:  # 헤더만 있거나 비어있음
        return 0
    pending = sum(1 for row in values[1:] if row and row[0].strip() == "대기")
    return pending


def get_existing_topics(service):
    """기존 에피소드의 주제 목록을 반환 (중복 방지용)"""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="D:D"  # 주제 컬럼
    ).execute()
    values = result.get("values", [])
    if len(values) <= 1:
        return []
    return [row[0].strip() for row in values[1:] if row and row[0].strip()]


def get_next_ep_number(service):
    """다음 에피소드 번호를 반환"""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID,
        range="B:B"  # EP 컬럼
    ).execute()
    values = result.get("values", [])
    if len(values) <= 1:
        return 1
    ep_nums = []
    for row in values[1:]:
        if row and row[0].strip():
            try:
                # "EP.024" -> 24
                num = int(row[0].strip().replace("EP.", "").replace("ep.", ""))
                ep_nums.append(num)
            except ValueError:
                continue
    return max(ep_nums, default=0) + 1


def generate_episodes(client, existing_topics, count, sleep=time.sleep):
    """OpenAI API로 새 에피소드를 만든다. 실패하면 지수 백오프로 재시도한다."""
    topics_text = "\n".join(f"- {t}" for t in existing_topics) if existing_topics else "(없음)"
    user_prompt = (
        f"아래는 이미 사용된 주제 목록입니다. 띄어쓰기와 '편'을 빼면 같은 주제, "
        f"또는 같은 훅은 만들지 마세요.\n\n{topics_text}\n\n"
        f"댓글이 남자 편/여자 편으로 갈리는 소재 {count}개를 만드세요. "
        f"절반 이상은 돈·소비, 온도차, 기념일, 전 애인, 새벽 연락, 리모컨 결입니다. "
        f"각 항목에 훅, 심리 한 줄, '남자쪽 / 여자쪽' 마무리 질문을 넣으세요.\n"
        f'출력은 {{"episodes": [에피소드 {count}개]}} JSON 하나입니다. 각 항목은 스키마 그대로 쓰세요.'
    )
    model = openai_model()
    request = {
        "model": model,
        "max_completion_tokens": 16000,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "episode_batch",
                "strict": True,
                "schema": EPISODE_JSON_SCHEMA,
            },
        },
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }

    print(f"  OpenAI API 호출 중... ({count}개 생성 요청, 모델 {model})")
    attempts = len(OPENAI_RETRY_BACKOFF_SECONDS) + 1
    last_error = None
    for attempt in range(attempts):
        try:
            response = client.chat.completions.create(**request)
            message = response.choices[0].message
            refusal = getattr(message, "refusal", None)
            if refusal:
                raise ValueError(f"모델이 생성을 거절했습니다: {refusal}")
            return parse_generated_payload(message.content)
        except Exception as exc:
            last_error = exc
            if attempt >= len(OPENAI_RETRY_BACKOFF_SECONDS):
                raise
            wait = OPENAI_RETRY_BACKOFF_SECONDS[attempt]
            print(f"  OpenAI 호출 실패, {wait}초 후 재시도 ({attempt + 1}회차): {exc}")
            sleep(wait)
    raise last_error


def validate_episode(ep, idx, existing_topics):
    """에피소드 유효성 검증. 통과하지 못한 항목은 행으로 만들지 않는다."""
    return [f"{idx}번째: {err}" for err in validate_generated_episode(ep, existing_topics)]


def episodes_to_rows(episodes, start_num, existing_topics):
    """검증을 통과한 에피소드만 시트 행으로 변환하고, 뽑는 중에도 중복을 뺀다."""
    rows = []
    seen = list(existing_topics)
    for ep in episodes:
        if validate_generated_episode(ep, seen):
            continue
        ep_label = f"EP.{start_num + len(rows):03d}"
        rows.append(generated_to_row(ep, ep_label))
        seen.append(ep.get("주제") or "")
    return rows


def ensure_hook_headers(service):
    """H·I가 비어 있을 때만 헤더를 쓴다. A–G 원문은 건드리지 않는다."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="H1:I1"
    ).execute()
    values = result.get("values", [])
    current = values[0] if values else []
    h = current[0].strip() if len(current) > 0 else ""
    i = current[1].strip() if len(current) > 1 else ""
    if h == "훅" and i == "심리":
        return
    if h or i:
        print(f"  H/I 헤더가 이미 있어 열 이름은 유지합니다: {current}")
        return
    service.spreadsheets().values().update(
        spreadsheetId=SHEET_ID,
        range="H1:I1",
        valueInputOption="RAW",
        body={"values": [["훅", "심리"]]},
    ).execute()
    print("  헤더 추가: H=훅, I=심리")


def append_original_topics(service):
    """N01–N20을 새 대기 행으로 붙인다. 이미 있으면 건너뛴다."""
    result = service.spreadsheets().values().get(
        spreadsheetId=SHEET_ID, range="A:I"
    ).execute()
    sheet_rows = result.get("values", [])
    rows = rows_for_append(sheet_rows)
    if not rows:
        print("  신규 원작 주제: 추가할 항목 없음 (이미 있거나 주제 중복)")
        return 0
    ensure_hook_headers(service)
    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    print(f"  ✅ 신규 원작 {len(rows)}개 추가: {rows[0][1]}~{rows[-1][1]}")
    return len(rows)


def print_plan():
    sheet_rows = fetch_public_rows()
    pending = sum(1 for row in sheet_rows[1:] if row and row[0].strip() == "대기")
    rows = rows_for_append(sheet_rows)
    print(f"대기 {pending}개. 새로 붙일 원작 {len(rows)}개.")
    for row in rows:
        print(f"  {row[1]} {row[2]} {row[3]} | {row[7]}")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--plan" in argv:
        return print_plan()

    print("=" * 50)
    print("그날의 남녀 - 에피소드 자동 보충")
    print("=" * 50)

    print("[1/4] Google Sheets 연결...")
    service = load_sheets_service()

    try:
        added = append_original_topics(service)
        print(f"  신규 원작 처리: {added}개")
    except Exception as exc:
        print(f"  신규 원작 추가 실패 (기존 대기분 처리는 계속): {exc}")

    pending = get_pending_count(service)
    print(f"  현재 '대기' 에피소드: {pending}개")

    if pending > REFILL_THRESHOLD:
        print(f"  임계값({REFILL_THRESHOLD}개) 이상이므로 보충 불필요. 종료.")
        return 0

    print("  ⚠ 임계값 이하! 보충 시작...")

    print(f"[2/4] OpenAI API로 {BATCH_SIZE}개 에피소드 생성...")
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY 환경변수가 필요합니다.")
    from openai import OpenAI
    client = OpenAI(api_key=api_key)

    existing_topics = get_existing_topics(service)
    print(f"  기존 주제 {len(existing_topics)}개 확인")

    episodes = generate_episodes(client, existing_topics, BATCH_SIZE)

    print("[3/4] 생성된 에피소드 검증...")
    kept = []
    dropped = 0
    seen = list(existing_topics)
    for i, ep in enumerate(episodes, start=1):
        errors = validate_episode(ep, i, seen)
        if errors:
            dropped += 1
            print(f"  제외: {errors[0]}")
            continue
        kept.append(ep)
        seen.append((ep.get("주제") or "").strip())
    if not kept:
        raise SystemExit("검증을 통과한 에피소드가 없습니다.")
    print(f"  ✅ {len(kept)}개 사용, {dropped}개 제외")

    print("[4/4] Google Sheets에 추가...")
    ensure_hook_headers(service)
    next_ep = get_next_ep_number(service)
    rows = episodes_to_rows(kept, next_ep, existing_topics)
    if not rows:
        raise SystemExit("시트에 쓸 행이 없습니다.")

    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows}
    ).execute()

    print(f"  ✅ 완료: {rows[0][1]}~{rows[-1][1]} ({len(rows)}개) 추가")
    print(f"  현재 '대기' 에피소드: {pending + len(rows)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
