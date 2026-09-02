"""
그날의 남녀 - 에피소드 자동 보충 스크립트

Google Sheets에서 "대기" 상태의 에피소드가 특정 임계값 이하로 떨어지면,
Claude API를 사용하여 새 에피소드 30개를 자동으로 생성하여 시트에 추가한다.

필요 환경변수:
  ANTHROPIC_API_KEY
  GOOGLE_SERVICE_ACCOUNT

필요 패키지:
  pip install anthropic google-auth google-api-python-client requests
"""

import os
import json
import csv
import io
import sys

import requests
from anthropic import Anthropic
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# === 설정 ===
SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# "대기" 에피소드가 이 값 이하로 떨어지면 보충 시작
REFILL_THRESHOLD = int(os.environ.get("REFILL_THRESHOLD", "30"))
# 한 번에 생성할 에피소드 수
BATCH_SIZE = int(os.environ.get("REFILL_BATCH_SIZE", "30"))

SYSTEM_PROMPT = """당신은 한국어 유튜브 콘텐츠 "그날의 남녀" 시리즈의 대본 작가입니다.
커플이 매일 사소한 일로 싸우는 에피소드를 작성합니다.

각 에피소드는 반드시 이 구조를 따릅니다:
- 화자: "남자" 또는 "여자" 또는 "중립" (번갈아 가며)
- 주제: 싸움의 원인이 된 짧은 주제 (2~4자, 예: 치약, 답장, 게임, 요리)
- 대본: "저희 오늘 또 싸웠습니다."로 시작하는 4~6문장의 1인칭 대본.
  구체적인 상황 묘사 → 상대방의 반응 → 대사("????" 라고 했습니다) → 결말(그래서 싸웠습니다)
  반드시 "그래서 싸웠습니다."로 끝나야 합니다.
- 마무리 질문: 시청자에게 던지는 질문 1줄 (물음표로 끝)
- Threads 글감: 한국어 또는 영어로 된 SNS용 짧은 글 1~2문장. "이거 진짜야?" 형태 권장

소재 예시: 치약, 답장, 저녁메뉴, 패션, 약속지연, 사진구도, 의심, 게임, 유통기한,
늦잠, 리뷰, 자리, 리모컨, 길찾기, 생일선물, 새벽통화, 김치찌개, 정산,
헤어스타일, 선물불균형, 커플사진, 전연인취향, 이성친구, 어깨동무, 다이어트,
질투, 200일, 데이트비용, 영화취향, 결혼, 우정vs연애, 인스타하트, 애교,
떡볶이, 사과, 데이트지각, 운전, 입맛, 집안일, 자리양보, 사진보정, 운전연수,
여행계획, 커플싸움, 원피스, 택배, 정치, 게임(지하철), 운동, 커피, 야간사진,
용돈, 산책, 꿈, 보조배터리, 메모장, 메뉴, 평행주차, 우산, 소비, 약속장소,
셀카각도, 온도차, 억양, 배송, 케이크, 기억, 지하철, 약속시간, 양파, 영화관,
알람, 졸업사진, 패션, 야식, 숙소, 머리감기, 좌석, PT, 카페, 비상금, 맛집,
강아지이름, 게임기, 공포영화, 택배개봉, 아침, 내비, 게임, 답장, 우유팩,
세일, 변기, 질투, 야근, 감정차이, 휴대폰(2개), 현금, 요리, 연인옷, 체중,
늦은귀가, 전애인, 접시, 사랑표현, 색번짐, 밥챙김, 남사친, 코골이, 솔직함,
서프라이즈, 앞머리, 데이트폰, 연인흉, 화장품, 칭찬, 단둘만남, 선물, 트림,
공감, 옷차림, 초대식사, 질투(아이돌), 시트교체, 일상보고, 과자부스러기,
하품, 전남친, 옷취향, 코풀기, 감정무시, 화장대, 관계불안, 약속, 돈사용,
방귀, 칭찬(사진), 강아지털, 화장, 질투(연예인)

중복되지 않는 완전히 새로운 소재를 만드세요.

반드시 JSON 배열만 출력하세요. 다른 설명이나 마크다운은 포함하지 마세요.
스키마: [{"화자": str, "주제": str, "대본": str, "마무리_질문": str, "Threads_글감": str}, ...]
"""


def load_sheets_service():
    """Google Sheets API 서비스 생성"""
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT")
    if not creds_json:
        raise SystemExit("GOOGLE_SERVICE_ACCOUNT 환경변수가 필요합니다.")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


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


def generate_episodes(client, existing_topics, count):
    """Claude API를 사용하여 새 에피소드 생성"""
    topics_text = "\n".join(f"- {t}" for t in existing_topics) if existing_topics else "(없음)"
    user_prompt = (
        f"아래는 이미 사용된 주제 목록입니다. 겹치지 않는 완전히 새로운 소재로 "
        f"{count}개를 만들어주세요.\n\n{topics_text}\n\n"
        f"JSON 배열 {count}개, 스키마 그대로 출력하세요."
    )

    print(f"  Claude API 호출 중... ({count}개 생성 요청)")
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    raw = "".join(block.text for block in message.content if block.type == "text")
    raw = raw.strip().removeprefix("```json").removesuffix("```").strip()
    data = json.loads(raw)

    if not isinstance(data, list):
        raise ValueError(f"예상치 못한 응답 형식: {type(data)}")

    return data


def validate_episode(ep, idx):
    """에피소드 유효성 검증"""
    errors = []
    required = ["화자", "주제", "대본", "마무리_질문", "Threads_글감"]
    for key in required:
        if key not in ep or not ep[key]:
            # underscore 버전도 시도
            alt_key = key.replace("_", " ")
            if alt_key not in ep or not ep[alt_key]:
                errors.append(f"{idx}번째: '{key}' 필드 누락")
    
    대본 = ep.get("대본", ep.get("대본", ""))
    if 대본 and "싸웠습니다" not in 대본:
        errors.append(f"{idx}번째: 대본에 '싸웠습니다' 포함 필요")
    
    return errors


def episodes_to_rows(episodes, start_num):
    """에피소드를 시트 행 형태로 변환"""
    rows = []
    for i, ep in enumerate(episodes):
        ep_label = f"EP.{start_num + i:03d}"
        화자 = ep.get("화자", ep.get("화자", "중립"))
        주제 = ep.get("주제", ep.get("주제", ""))
        대본 = ep.get("대본", ep.get("대본", ""))
        질문 = ep.get("마무리_질문", ep.get("마무리 질문", ""))
        threads = ep.get("Threads_글감", ep.get("Threads 글감", ""))
        rows.append(["대기", ep_label, 화자, 주제, 대본, 질문, threads])
    return rows


def main():
    print("=" * 50)
    print("그날의 남녀 - 에피소드 자동 보충")
    print("=" * 50)

    # 1. Google Sheets 연결
    print("[1/4] Google Sheets 연결...")
    service = load_sheets_service()

    # 2. 현재 상태 확인
    pending = get_pending_count(service)
    print(f"  현재 '대기' 에피소드: {pending}개")

    if pending > REFILL_THRESHOLD:
        print(f"  임계값({REFILL_THRESHOLD}개) 이상이므로 보충 불필요. 종료.")
        return

    print(f"  ⚠ 임계값 이하! 보충 시작...")

    # 3. Claude API로 새 에피소드 생성
    print(f"[2/4] Claude API로 {BATCH_SIZE}개 에피소드 생성...")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY 환경변수가 필요합니다.")
    client = Anthropic(api_key=api_key)

    existing_topics = get_existing_topics(service)
    print(f"  기존 주제 {len(existing_topics)}개 확인")

    episodes = generate_episodes(client, existing_topics, BATCH_SIZE)

    # 4. 검증
    print(f"[3/4] 생성된 에피소드 검증...")
    all_errors = []
    for i, ep in enumerate(episodes, start=1):
        all_errors.extend(validate_episode(ep, i))
    if all_errors:
        print(f"  검증 실패:")
        for e in all_errors[:10]:
            print(f"    - {e}")
        raise SystemExit("검증 실패로 종료")

    print(f"  ✅ {len(episodes)}개 검증 통과")

    # 5. 시트에 추가
    print(f"[4/4] Google Sheets에 추가...")
    next_ep = get_next_ep_number(service)
    rows = episodes_to_rows(episodes, next_ep)

    service.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range="A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows}
    ).execute()

    print(f"  ✅ 완료: EP.{next_ep:03d}~EP.{next_ep + len(rows) - 1:03d} ({len(rows)}개) 추가")
    print(f"  현재 '대기' 에피소드: {pending + len(rows)}개")


if __name__ == "__main__":
    main()
