"""
그날의남녀 '우리 오늘 화해했어요' 콘텐츠 큐.

- data/reconcile_episodes.json 을 시트보다 먼저 쓴다. 시트는 읽기만 하고, 큐에서 나간 회차는
  시트 Status 를 바꾸지 않는다(기존 행 삭제·수정 없음).
- 저장소에 상태를 커밋하지 않아도 되도록, 회차는 '예약 슬롯 번호'로 정한다.
  UTC 날짜 기준 하루 2슬롯: 오전(UTC 00:00 크론 = KST 09:00) / 저녁(UTC 12:00 크론 = KST 21:00).
  GitHub 크론이 몇 시간 늦어도 같은 UTC 날짜·같은 반나절 안에 돌기 때문에 슬롯이 흔들리지 않는다.
- 큐가 끝나면(슬롯 번호 ≥ 큐 길이) 기존처럼 시트의 '대기' 행으로 돌아간다.
"""
import json
import os
from datetime import date, datetime, timezone

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
QUEUE_PATH = os.path.join(DATA_DIR, "reconcile_episodes.json")
# 슬롯 0 = 2026-10-16 오전(KST 09:00 크론) 실행.
QUEUE_START = date(2026, 10, 16)

SHOP_URL = "https://www.avoir24.com"
SHOP_LINE = "그날 입은 옷이 궁금하다면 → 아브아르 AVOIR " + SHOP_URL
DEFAULT_QUESTION = "여러분은 어떻게 화해해요?"
DEFAULT_WARM = "싸운 날보다 화해한 날을 더 오래 기억하게 돼요."

YOUTUBE_TAGS = [
    "커플화해",
    "화해썰",
    "연애썰",
    "커플일상",
    "커플공감",
    "연애심리",
    "커플코디",
    "그날의남녀",
    "쇼츠",
]
HASHTAGS = "#커플화해 #화해썰 #연애썰 #커플일상 #그날의남녀"


def load_queue(path=QUEUE_PATH):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def slot_index(now=None, start=QUEUE_START):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    days = (now.date() - start).days
    return days * 2 + (1 if now.hour >= 12 else 0)


def episode_label(item):
    num = int("".join(ch for ch in item["id"] if ch.isdigit()) or 0)
    return f"화해 EP.{num:02d}"


def to_episode(item):
    """큐 항목 → main.py 가 쓰는 ep dict (시트 행에서 만든 것과 같은 키)."""
    speaker_ko = item.get("speaker", "여자")
    speaker = "male" if speaker_ko == "남자" else "female"
    question = item.get("question") or DEFAULT_QUESTION
    return {
        "ep": item["id"],
        "label": episode_label(item),
        "source": "reconcile",
        "gender": speaker_ko,
        "speaker": speaker,
        "speaker_ko": speaker_ko,
        "topic": item.get("topic", ""),
        "fight": item["fight"].strip(),
        "makeup": item["makeup"].strip(),
        "script": item["fight"].strip() + "\n\n" + item["makeup"].strip(),
        "question": question,
        "sheet_question": question,
        "hook": item["hook"].strip(),
        "psychology": (item.get("warm") or DEFAULT_WARM).strip(),
        "outfit": item.get("outfit", ""),
        "shop": bool(item.get("shop")),
        "poll_male": "",
        "poll_female": "",
        "row_num": None,
    }


def next_queue_episode(now=None, queue=None, index=None):
    queue = load_queue() if queue is None else queue
    idx = slot_index(now) if index is None else index
    if idx < 0 or idx >= len(queue):
        return None
    return to_episode(queue[idx])


def build_threads_text(ep):
    parts = [ep["script"], ep["psychology"], ep["question"]]
    if ep.get("shop"):
        parts.append(SHOP_LINE)
    text = "\n\n".join(p for p in parts if p)
    if len(text) > 500:
        parts = [ep["script"], ep["question"]] + ([SHOP_LINE] if ep.get("shop") else [])
        text = "\n\n".join(parts)
    return text[:500]


def build_title(ep, limit=100):
    suffix = f" | 그날의남녀 {ep['label']}"
    hook = " ".join(ep["hook"].split())
    if len(hook) + len(suffix) > limit:
        hook = hook[: limit - len(suffix) - 1].rstrip() + "…"
    return hook + suffix


def build_description(ep):
    lines = [ep["hook"], "", "그리고 저희는 이렇게 화해했어요.", "", ep["psychology"], "",
             ep["question"], "댓글로 여러분만의 화해법을 알려 주세요."]
    if ep.get("shop"):
        lines += ["", SHOP_LINE]
    lines += ["", HASHTAGS]
    return "\n".join(lines).strip()
