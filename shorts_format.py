"""
그날의남녀 Shorts 포맷.

시트 원문(Status, EP, 화자, 주제, 대본, 마무리 질문)은 수정하지 않는다.
EP.078–107 훅·투표·심리는 data/ep078_107_overrides.csv 를 생성 시점에 덮어 쓰고,
N01–N20 은 새 대기 행으로만 붙인다.
"""
import csv
import os
import re
from dataclasses import dataclass

W, H = 1080, 1920
# YouTube Shorts UI: 상단 검색, 우측 버튼, 하단 제목·설명.
SAFE_LEFT = 72
SAFE_RIGHT = 880
SAFE_TOP = 250
SAFE_BOTTOM = 1360
STROKE = 4
YT_TITLE_MAX = 100

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OVERRIDES_CSV = os.path.join(DATA_DIR, "ep078_107_overrides.csv")
TOPICS_CSV = os.path.join(DATA_DIR, "topics_n01_n20.csv")

# 남성은 낮고 느리게, 여성은 높고 또렷하게. 같은 성우를 쓰지 않는다.
VOICES = {
    "male": {"languageCode": "ko-KR", "name": "ko-KR-Wavenet-C"},
    "female": {"languageCode": "ko-KR", "name": "ko-KR-Neural2-A"},
}
AUDIO_CONFIGS = {
    "male": {"audioEncoding": "MP3", "speakingRate": 1.02, "pitch": -3.0, "sampleRateHertz": 44100},
    "female": {"audioEncoding": "MP3", "speakingRate": 1.08, "pitch": 2.0, "sampleRateHertz": 44100},
}

YOUTUBE_TAGS = [
    "커플싸움",
    "연애심리",
    "남녀심리",
    "데이트갈등",
    "커플공감",
    "남자편",
    "여자편",
    "연애고민",
    "그날의남녀",
    "쇼츠",
]

FORBIDDEN_TAG_SNIPPETS = (
    "영어공부",
    "영어회화",
    "영어학습",
    "영문법",
    "english",
    "learn english",
)

DEFAULT_PSYCH = "같은 하루라도 서운한 지점이 다르면, 누가 맞는지보다 어디가 아팠는지를 보게 돼요."

MALE_MARKERS = ("남자친구", "남친", "남편")
FEMALE_MARKERS = ("여자친구", "여친", "아내")

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

YELLOW = (255, 215, 0)
WHITE = (255, 255, 255)
PINK = (255, 140, 170)
BLUE = (130, 170, 255)


@dataclass
class Shot:
    role: str
    display: str
    spoken: str
    voice: str
    stick: str
    expr: str
    min_duration: float = 0.0
    poll_male: str = ""
    poll_female: str = ""
    extra_display: str = ""


@dataclass
class Topic:
    id: str
    topic: str
    speaker_ko: str
    hook: str
    summary: str
    psychology: str
    poll_male: str
    poll_female: str
    raw_poll: str = ""


def find_font():
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(f"한글 폰트를 찾을 수 없습니다. 후보: {FONT_CANDIDATES}")


def youtube_upload_enabled(env=None):
    """환경변수가 없으면 기존처럼 업로드한다. 워크플로는 21:00 KST에만 true."""
    env = os.environ if env is None else env
    raw = env.get("YOUTUBE_UPLOAD")
    if raw is None:
        return True
    return raw.strip().lower() in {"1", "true", "yes", "y"}


def _read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def parse_poll(text):
    """'남자쪽 👈 / 👉 여자쪽' 또는 '남자쪽 / 여자쪽' → (남자편, 여자편)."""
    if not text:
        return None
    raw = text.strip()
    if raw in {"-", "—", "–"}:
        return None
    raw = raw.replace("👈", " ").replace("👉", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    if "/" not in raw:
        return None
    left, right = raw.split("/", 1)
    left = left.strip(" -\t")
    right = right.strip(" -\t")
    if not left or not right:
        return None
    return left, right


def load_overrides(path=OVERRIDES_CSV):
    overrides = {}
    for row in _read_csv(path):
        ep = (row.get("EP") or "").strip()
        parsed = parse_poll(row.get("투표") or "")
        if not ep or not parsed:
            continue
        overrides[ep] = {
            "hook": (row.get("훅") or "").strip(),
            "poll_male": parsed[0],
            "poll_female": parsed[1],
            "psychology": (row.get("심리") or "").strip(),
            "topic": (row.get("원 주제") or "").strip(),
        }
    return overrides


def _topic_name(subtitle):
    name = (subtitle or "").strip()
    if name.endswith("편"):
        name = name[:-1].strip()
    return name


def load_topics(path=TOPICS_CSV):
    topics = []
    for row in _read_csv(path):
        parsed = parse_poll(row.get("투표") or "")
        if not parsed:
            continue
        subtitle = (row.get("부제") or "").strip()
        topics.append(Topic(
            id=(row.get("ID") or "").strip(),
            topic=_topic_name(subtitle),
            speaker_ko=(row.get("화자") or "여자").strip(),
            hook=(row.get("훅") or "").strip(),
            summary=(row.get("싸움 요약") or "").strip(),
            psychology=(row.get("심리 한 줄") or "").strip(),
            poll_male=parsed[0],
            poll_female=parsed[1],
            raw_poll=(row.get("투표") or "").strip(),
        ))
    return topics


_OVERRIDES = None
_TOPICS = None


def overrides():
    global _OVERRIDES
    if _OVERRIDES is None:
        _OVERRIDES = load_overrides()
    return _OVERRIDES


def topics():
    global _TOPICS
    if _TOPICS is None:
        _TOPICS = load_topics()
    return _TOPICS


def normalize_key(text):
    text = text or ""
    text = text.replace(" ", "").replace("편", "")
    text = re.sub(r"[^\w가-힣]", "", text, flags=re.UNICODE)
    return text.casefold()


def is_duplicate_topic(topic, existing_topics):
    key = normalize_key(topic)
    if not key:
        return True
    return key in {normalize_key(t) for t in existing_topics if t}


def split_sentences(script):
    text = (script or "").replace("\n", " ").strip()
    parts = re.split(r"(?<=[\.!?…])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def fallback_hook(topic, script):
    for sentence in split_sentences(script):
        if "저희 오늘 또 싸웠" in sentence:
            continue
        quote = re.search(r"[\"“「](.+?)[\"”」]", sentence)
        if quote and 6 <= len(quote.group(1)) <= 32:
            return quote.group(1)
    topic = (topic or "오늘 싸움").strip()
    return f"{topic}, 그래서 또 싸웠습니다"


def _first_index(text, markers):
    found = None
    for marker in markers:
        idx = text.find(marker)
        if idx >= 0 and (found is None or idx < found):
            found = idx
    return found


def sentence_voice(sentence, speaker):
    male_at = _first_index(sentence, MALE_MARKERS)
    female_at = _first_index(sentence, FEMALE_MARKERS)
    if male_at is None and female_at is None:
        return speaker
    if female_at is None or (male_at is not None and male_at < female_at):
        return "male"
    return "female"


def guess_expr(sentence):
    if any(w in sentence for w in ["싸웠", "화", "짜증", "한숨"]):
        return "angry"
    if any(w in sentence for w in ['"', "라고 했", "말했"]):
        return "talking"
    if any(w in sentence for w in ["서운", "슬", "울", "힘들"]):
        return "sad"
    if any(w in sentence for w in ["놀", "헐", "뭐", "없"]):
        return "surprised"
    return "neutral"


def _match_topic(topic, hook):
    key = normalize_key(topic)
    hook = (hook or "").strip()
    for item in topics():
        if hook and hook == item.hook:
            return item
        if key and key == normalize_key(item.topic):
            return item
    return None


def prepare_episode(row):
    """시트 행(dict)에 생성용 훅·심리·투표를 붙인다. 원문 필드는 sheet_* 로 남긴다."""
    ep_id = (row.get("EP") or "").strip()
    speaker_ko = (row.get("화자") or "").strip()
    speaker = "male" if speaker_ko == "남자" else "female"
    topic = (row.get("주제") or "").strip()
    script = (row.get("대본") or "").strip()
    question = (row.get("마무리 질문") or "").strip()
    sheet_hook = (row.get("훅") or "").strip()
    sheet_psych = (row.get("심리") or "").strip()

    override = overrides().get(ep_id) or {}
    matched = _match_topic(topic, sheet_hook)

    hook = sheet_hook or override.get("hook") or (matched.hook if matched else "") or fallback_hook(topic, script)
    psychology = (
        sheet_psych
        or override.get("psychology")
        or (matched.psychology if matched else "")
        or DEFAULT_PSYCH
    )
    poll_male = override.get("poll_male") or (matched.poll_male if matched else "")
    poll_female = override.get("poll_female") or (matched.poll_female if matched else "")
    if not (poll_male and poll_female):
        parsed = parse_poll(question)
        if parsed:
            poll_male, poll_female = parsed

    return {
        "ep": ep_id,
        "gender": speaker_ko or ("남자" if speaker == "male" else "여자"),
        "speaker": speaker,
        "speaker_ko": speaker_ko or ("남자" if speaker == "male" else "여자"),
        "topic": topic,
        "script": script,
        "question": _poll_display(poll_male, poll_female) or question,
        "sheet_question": question,
        "hook": hook,
        "psychology": psychology,
        "poll_male": poll_male,
        "poll_female": poll_female,
        "row_num": row.get("row_num"),
    }


def _poll_display(poll_male, poll_female):
    if poll_male and poll_female:
        return f"{poll_male} 👈 / 👉 {poll_female}"
    return ""


def build_title(hook, ep):
    hook = " ".join((hook or "").split())
    suffix = f" | 그날의남녀 {ep}"
    if not hook:
        hook = "오늘 또 싸운 이유"
    if len(hook) + len(suffix) > YT_TITLE_MAX:
        room = YT_TITLE_MAX - len(suffix) - 1
        hook = hook[: max(room, 0)].rstrip() + "…"
    title = hook + suffix
    if title.startswith("저희 오늘 또 싸웠습니다"):
        title = title.replace("저희 오늘 또 싸웠습니다", hook, 1)
    return title


def contains_english_learning(text):
    low = (text or "").lower()
    return any(snippet in low for snippet in FORBIDDEN_TAG_SNIPPETS)


def build_description(ep):
    poll_male = ep.get("poll_male") or "남자 편"
    poll_female = ep.get("poll_female") or "여자 편"
    lines = [
        ep.get("hook") or "",
        "",
        ep.get("psychology") or DEFAULT_PSYCH,
        "",
        "댓글로 편을 갈라 주세요.",
        f"남자 편: {poll_male}",
        f"여자 편: {poll_female}",
        "",
        "#커플싸움 #연애심리 #남녀심리 #데이트갈등 #남자편여자편 #연애고민 #그날의남녀",
    ]
    text = "\n".join(lines).strip()
    if contains_english_learning(text):
        raise ValueError("설명에 영어학습 태그가 포함되어 있습니다.")
    return text


def build_threads_text(ep):
    """대본 전문은 유지하고, 끝에 심리 한 줄과 편 가르기 질문만 붙인다."""
    script = (ep.get("script") or "").strip()
    psychology = (ep.get("psychology") or "").strip()
    if ep.get("poll_male") and ep.get("poll_female"):
        poll = f"남자 편: {ep['poll_male']}\n여자 편: {ep['poll_female']}"
    else:
        poll = (ep.get("sheet_question") or ep.get("question") or "").strip()
    parts = [script, psychology, "여러분은 어느 쪽인가요?", poll]
    text = "\n\n".join(p for p in parts if p)
    if len(text) > 500:
        parts = [script, "여러분은 어느 쪽인가요?", poll]
        text = "\n\n".join(p for p in parts if p)
    if len(text) > 500:
        text = text[:497] + "..."
    return text


def build_lines(ep):
    speaker = ep["speaker"]
    partner = "female" if speaker == "male" else "male"
    lines = [
        Shot(
            role="hook",
            display=ep["hook"],
            spoken=ep["hook"],
            voice=speaker,
            stick="both",
            expr="angry",
            min_duration=2.0,
        )
    ]
    for sentence in split_sentences(ep.get("script") or ""):
        if "저희 오늘 또 싸웠" in sentence:
            continue
        voice = sentence_voice(sentence, speaker)
        lines.append(Shot(
            role="body",
            display=sentence,
            spoken=sentence,
            voice=voice,
            stick=voice,
            expr=guess_expr(sentence),
        ))
    lines.append(Shot(
        role="psych",
        display=ep.get("psychology") or DEFAULT_PSYCH,
        spoken=ep.get("psychology") or DEFAULT_PSYCH,
        voice=partner,
        stick=partner,
        expr="neutral",
        min_duration=1.2,
    ))
    poll_male = ep.get("poll_male") or ""
    poll_female = ep.get("poll_female") or ""
    if poll_male and poll_female:
        male_spoken = f"남자 편. {poll_male}"
        female_spoken = f"여자 편. {poll_female}"
        extra = ""
    else:
        male_spoken = "남자 편입니다. 댓글로 알려 주세요."
        female_spoken = "여자 편입니다. 여러분은 어느 쪽인가요?"
        extra = ep.get("sheet_question") or ""
    lines.append(Shot(
        role="poll",
        display="남자 편 vs 여자 편",
        spoken=male_spoken,
        voice="male",
        stick="both",
        expr="surprised",
        min_duration=1.4,
        poll_male=poll_male,
        poll_female=poll_female,
        extra_display=extra,
    ))
    lines.append(Shot(
        role="poll",
        display="남자 편 vs 여자 편",
        spoken=female_spoken,
        voice="female",
        stick="both",
        expr="surprised",
        min_duration=1.4,
        poll_male=poll_male,
        poll_female=poll_female,
        extra_display=extra,
    ))
    return lines


def voices_in(lines):
    return {line.voice for line in lines}


def wrap_text(text, measure, max_width):
    lines = []
    for para in (text or "").split("\n"):
        if para == "":
            lines.append("")
            continue
        buf = ""
        for ch in para:
            trial = buf + ch
            if buf and measure(trial) > max_width:
                lines.append(buf)
                buf = ch
            else:
                buf = trial
        if buf or not lines or lines[-1] != "":
            lines.append(buf)
    while lines and lines[-1] == "":
        lines.pop()
    return lines or [""]


def _measure(font, text):
    if not text:
        return 0
    bbox = font.getbbox(text)
    return bbox[2] - bbox[0]


def fit_block(text, font_path, start, minimum, max_lines, top):
    from PIL import ImageFont

    max_width = SAFE_RIGHT - SAFE_LEFT - 2 * STROKE
    size = start
    font = None
    lines = [text or ""]
    line_height = start
    while size >= minimum:
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text(text, lambda s, font=font: _measure(font, s), max_width)
        line_height = size + max(14, size // 4)
        block_h = line_height * max(1, len(lines))
        if len(lines) <= max_lines and top + block_h <= SAFE_BOTTOM:
            break
        size -= 2
    else:
        font = ImageFont.truetype(font_path, minimum)
        lines = wrap_text(text, lambda s, font=font: _measure(font, s), max_width)
        line_height = minimum + max(14, minimum // 4)
    center = (SAFE_LEFT + SAFE_RIGHT) / 2
    placed = []
    y = top
    for line in lines:
        width = _measure(font, line)
        x = center - width / 2
        x = max(SAFE_LEFT, min(x, SAFE_RIGHT - width))
        placed.append({"text": line, "x": x, "y": y, "w": width})
        y += line_height
    return font, placed, y


def layout_card(shot, font_path):
    """카드에 그릴 (font, placed, fill) 목록. 좌표는 안전 영역 안."""
    if shot.role == "hook":
        font, placed, _ = fit_block(shot.display, font_path, 84, 52, 4, SAFE_TOP + 30)
        return [(font, placed, YELLOW)]
    if shot.role == "psych":
        y = SAFE_TOP + 20
        label_font, label, y = fit_block("한 줄 심리", font_path, 40, 32, 1, y)
        font, placed, _ = fit_block(shot.display, font_path, 54, 40, 5, y + 8)
        return [(label_font, label, YELLOW), (font, placed, WHITE)]
    if shot.role == "poll":
        blocks = [("남자 편 vs 여자 편", 72, 48, YELLOW, 2)]
        if shot.poll_male and shot.poll_female:
            blocks.extend([
                ("남자 편", 42, 32, BLUE, 1),
                (shot.poll_male, 52, 36, WHITE, 3),
                ("여자 편", 42, 32, PINK, 1),
                (shot.poll_female, 52, 36, WHITE, 3),
            ])
        elif shot.extra_display:
            blocks.append((shot.extra_display, 48, 36, WHITE, 4))
        y = SAFE_TOP + 10
        laid = []
        for text, start, minimum, fill, max_lines in blocks:
            font, placed, y = fit_block(text, font_path, start, minimum, max_lines, y)
            laid.append((font, placed, fill))
            y += 16
        return laid
    font, placed, _ = fit_block(shot.display, font_path, 54, 40, 6, SAFE_TOP + 40)
    return [(font, placed, WHITE)]


def _draw_stroked(draw, x, y, text, font, fill):
    for dx in range(-STROKE, STROKE + 1):
        for dy in range(-STROKE, STROKE + 1):
            if dx or dy:
                draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=fill)


def render_card(shot, font_path=None):
    from PIL import Image, ImageDraw

    font_path = font_path or find_font()
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    draw = ImageDraw.Draw(img)
    for font, placed, fill in layout_card(shot, font_path):
        for item in placed:
            if not item["text"]:
                continue
            _draw_stroked(draw, item["x"], item["y"], item["text"], font, fill)
    return img


def bright_bbox(image, threshold=40):
    pixels = image.convert("RGB").load()
    w, h = image.size
    min_x, min_y, max_x, max_y = w, h, -1, -1
    for y in range(h):
        for x in range(w):
            r, g, b = pixels[x, y]
            if r > threshold or g > threshold or b > threshold:
                if x < min_x:
                    min_x = x
                if y < min_y:
                    min_y = y
                if x > max_x:
                    max_x = x
                if y > max_y:
                    max_y = y
    if max_x < 0:
        return None
    return min_x, min_y, max_x, max_y


def bbox_inside_safe(bbox):
    if bbox is None:
        return False
    min_x, min_y, max_x, max_y = bbox
    return (
        min_x >= SAFE_LEFT - STROKE
        and max_x <= SAFE_RIGHT + STROKE
        and min_y >= SAFE_TOP - STROKE
        and max_y <= SAFE_BOTTOM + STROKE
    )


def compose_script(summary, spoken_line):
    summary = (summary or "").strip().rstrip(".")
    spoken_line = (spoken_line or "").strip().strip('"')
    return (
        f"저희 오늘 또 싸웠습니다. {summary}. "
        f"\"{spoken_line}\" 라고 했습니다. 그래서 싸웠습니다."
    )


def topic_to_row(item, ep_label):
    """새 대기 행. A–G 원문 열 뒤에 훅·심리를 둔다."""
    script = compose_script(item.summary, item.poll_female if item.speaker_ko == "여자" else item.poll_male)
    question = f"{item.poll_male} 👈 / 👉 {item.poll_female}"
    threads = f"[{item.id}] {item.hook}\n남자 편: {item.poll_male}\n여자 편: {item.poll_female}"
    return [
        "대기",
        ep_label,
        item.speaker_ko,
        item.topic,
        script,
        question,
        threads,
        item.hook,
        item.psychology,
    ]


def select_topics_to_append(sheet_rows, catalog=None):
    """기존 주제·훅·[Nid] 표식과 겹치면 건너뛴다."""
    catalog = topics() if catalog is None else catalog
    existing_topics = []
    blob_parts = []
    for row in sheet_rows:
        if row and row[0] in {"Status", "status"}:
            continue
        existing_topics.append(row[3] if len(row) > 3 else "")
        blob_parts.append(" ".join(row))
    blob = "\n".join(blob_parts)
    chosen = []
    used = list(existing_topics)
    for item in catalog:
        if item.id and f"[{item.id}]" in blob:
            continue
        if item.hook and item.hook in blob:
            continue
        if is_duplicate_topic(item.topic, used):
            continue
        chosen.append(item)
        used.append(item.topic)
    return chosen


def next_episode_number(sheet_rows):
    best = 0
    for row in sheet_rows:
        if len(row) < 2:
            continue
        raw = row[1].strip()
        match = re.fullmatch(r"[Ee][Pp]\.(\d+)", raw)
        if match:
            best = max(best, int(match.group(1)))
    return best + 1


def rows_for_append(sheet_rows, catalog=None):
    chosen = select_topics_to_append(sheet_rows, catalog)
    start = next_episode_number(sheet_rows)
    rows = []
    for offset, item in enumerate(chosen):
        rows.append(topic_to_row(item, f"EP.{start + offset:03d}"))
    return rows


REFILL_SYSTEM_PROMPT = """당신은 한국어 유튜브 쇼츠 "그날의남녀"의 대본 작가입니다.
댓글이 남자 편과 여자 편으로 갈리는 커플 싸움만 씁니다.

잘 되는 결 (배치의 절반 이상을 이 결로):
- 돈·소비 갈등 (정산, 비상금, 데이트 비용, 선물 가격)
- 온도차 (에어컨, 전기장판, 이불)
- 기념일 (100일, 1000일, 생일, 기념일 망각)
- 전 애인 (물건, 사진, 계정, 결혼식)
- 새벽 연락·새벽 통화
- 리모컨·집안 주도권처럼 사소한 주도권 싸움

피할 결: 트림, 코풀기, 하품, 방귀처럼 편이 안 갈리는 생활 소음.
영어 공부, 영어 회화 소재는 쓰지 않습니다.

각 에피소드:
- 화자: "남자" 또는 "여자" (번갈아)
- 주제: 2~8자. 이미 있는 주제와 띄어쓰기·'편'을 빼면 같으면 안 됩니다.
- 훅: 첫 2초에 띄울 한 줄. 갈등 대상(돈·물건·사람)과 숫자를 넣고, "저희 오늘 또 싸웠습니다"로 시작하지 않습니다.
- 대본: "저희 오늘 또 싸웠습니다."로 시작해 "그래서 싸웠습니다."로 끝나는 4~6문장.
  상황 → 상대 반응 → 대사("…") → 결말.
- 심리: 커플 심리 한 문장. 누가 나쁜지가 아니라 왜 서운한지.
- 마무리_질문: 반드시 "남자쪽 주장 / 여자쪽 주장" 형식. "~까요?"로만 끝내지 않습니다.
- Threads_글감: 한국어 1~2문장. 영어 학습 문구 금지.

반드시 아래 스키마의 JSON 객체만 출력하세요. 다른 키는 넣지 마세요.
{"episodes": [{"화자": str, "주제": str, "훅": str, "대본": str, "심리": str, "마무리_질문": str, "Threads_글감": str}, ...]}
"""


def validate_generated_episode(ep, existing_topics):
    errors = []
    required = ["화자", "주제", "훅", "대본", "심리", "마무리_질문", "Threads_글감"]
    for key in required:
        if not (ep.get(key) or "").strip():
            errors.append(f"'{key}' 없음")
    speaker = (ep.get("화자") or "").strip()
    if speaker not in {"남자", "여자"}:
        errors.append("화자는 남자 또는 여자")
    hook = (ep.get("훅") or "").strip()
    if hook.startswith("저희 오늘"):
        errors.append("훅이 고정 인사로 시작")
    question = (ep.get("마무리_질문") or "").strip()
    if parse_poll(question) is None:
        errors.append("마무리_질문이 남자편 / 여자편 형식이 아님")
    script = (ep.get("대본") or "").strip()
    if script and "싸웠습니다" not in script:
        errors.append("대본에 싸웠습니다 없음")
    if is_duplicate_topic(ep.get("주제") or "", existing_topics):
        errors.append("기존 주제와 중복")
    threads = ep.get("Threads_글감") or ""
    if contains_english_learning(threads) or contains_english_learning(hook):
        errors.append("영어학습 소재")
    return errors


def generated_to_row(ep, ep_label):
    question = (ep.get("마무리_질문") or "").strip()
    parsed = parse_poll(question)
    if parsed and "👈" not in question:
        question = f"{parsed[0]} 👈 / 👉 {parsed[1]}"
    return [
        "대기",
        ep_label,
        (ep.get("화자") or "여자").strip(),
        (ep.get("주제") or "").strip(),
        (ep.get("대본") or "").strip(),
        question,
        (ep.get("Threads_글감") or "").strip(),
        (ep.get("훅") or "").strip(),
        (ep.get("심리") or "").strip(),
    ]
