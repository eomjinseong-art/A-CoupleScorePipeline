"""
그날의 남녀 - 일일 자동화 파이프라인 (GitHub Actions용)
전체 흐름: Google Sheets에서 대본 읽기 → 영상 생성 → YouTube 업로드 → Threads 포스팅
"""
import os, json, time, base64, subprocess, sys, shutil, csv, io
import requests
from PIL import Image, ImageDraw, ImageFont
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# === 환경 변수 ===
GCP_TTS_KEY = os.environ.get("GCP_TTS_KEY")
YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")
THREADS_TOKEN = os.environ.get("THREADS_TOKEN")
THREADS_USER_ID = "27227055083638713"
GOOGLE_SERVICE_ACCOUNT = os.environ.get("GOOGLE_SERVICE_ACCOUNT")  # JSON string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
WORK_DIR = "/tmp/couple_render"

# Google Sheets (공개 읽기)
SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"

TTS_URL = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GCP_TTS_KEY}"
VOICES = {
    "male": {"languageCode": "ko-KR", "name": "ko-KR-Wavenet-C"},
    "female": {"languageCode": "ko-KR", "name": "ko-KR-Neural2-A"},
    "neutral": {"languageCode": "ko-KR", "name": "ko-KR-Wavenet-C"},
}
AUDIO_CONFIGS = {
    "male": {"audioEncoding": "MP3", "speakingRate": 1.15, "pitch": 0.0, "sampleRateHertz": 44100},
    "female": {"audioEncoding": "MP3", "speakingRate": 1.1, "pitch": -1.0, "sampleRateHertz": 44100},
    "neutral": {"audioEncoding": "MP3", "speakingRate": 1.15, "pitch": 0.0, "sampleRateHertz": 44100}
}

CLOSING_TEXT = "여러분은 어떠신가요?\n매일 남녀의 속마음을\n같이 들여다보아요.\n오늘도 사랑하세요."
COVER_DURATION = 4.0
CLOSING_DURATION = 4.0
SAMPLE_RATE = 44100
W, H = 1080, 1920
BG = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (255, 215, 0)
PINK = (255, 140, 170)
BLUE = (130, 170, 255)
RED = (255, 90, 90)

# 폰트 경로 탐색 (여러 위치 지원)
_font_candidates = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
]
font_path = None
for _fp in _font_candidates:
    if os.path.exists(_fp):
        font_path = _fp
        break
if not font_path:
    raise FileNotFoundError(f"Noto Sans CJK 폰트를 찾을 수 없습니다. 후보: {_font_candidates}")

font_title = ImageFont.truetype(font_path, 64)
font_topic = ImageFont.truetype(font_path, 48)
font_body = ImageFont.truetype(font_path, 46)
font_closing = ImageFont.truetype(font_path, 44)


# ===== Google Sheets 읽기 =====

def fetch_next_episode():
    """Google Sheets에서 Status='대기'인 첫 번째 에피소드를 가져옴"""
    print("[1/7] Google Sheets에서 대본 읽기...")
    resp = requests.get(SHEET_CSV_URL)
    if resp.status_code != 200:
        print(f"  Sheets 접근 실패: {resp.status_code}")
        return None

    # UTF-8 BOM(﻿) 포함 디코딩 — Google Sheets CSV는 BOM 포함
    content = resp.content.decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(content))
    print(f"  CSV 헤더: {reader.fieldnames}")
    for row_idx, row in enumerate(reader, start=2):  # 헤더=1행, 데이터=2행부터
        if row.get("Status", "").strip() == "대기":
            ep = {
                "ep": row.get("EP", "").strip(),
                "gender": "male" if row.get("화자", "").strip() == "남자" else "female",
                "topic": row.get("주제", "").strip(),
                "script": row.get("대본", "").strip(),
                "question": row.get("마무리 질문", "").strip(),
                "threads_text": row.get("Threads 글감", "").strip(),
                "row_num": row_idx,  # Sheets 행 번호 (상태 업데이트용)
            }
            # 대본을 문장으로 분리
            sentences = [s.strip() for s in ep["script"].replace(". ", ".\n").split("\n") if s.strip()]
            ep["sentences"] = sentences
            print(f"  대상: {ep['ep']} ({ep['gender']}) - {ep['topic']}편 (행 {row_idx})")
            return ep
    print("  ⚠ 대기 상태 에피소드 없음 — 모든 에피소드가 '완료' 상태입니다.")
    return None


# ===== 막대기 캐릭터 =====

def draw_stick(draw, cx, cy, gender, expr="neutral", scale=1.0):
    s = scale
    hr, bl, al, ll, lw = int(50*s), int(130*s), int(85*s), int(110*s), int(6*s)
    hc = (cx, cy - ll - bl - hr)
    bt = (cx, cy - ll - bl)
    bb = (cx, cy - ll)
    color = BLUE if gender == "male" else PINK

    draw.ellipse([hc[0]-hr, hc[1]-hr, hc[0]+hr, hc[1]+hr], outline=WHITE, width=lw)
    if gender == "female":
        draw.line([(hc[0]-hr+int(5*s), hc[1]-int(10*s)), (hc[0]-hr-int(15*s), hc[1]+hr+int(40*s))], fill=color, width=int(5*s))
        draw.line([(hc[0]+hr-int(5*s), hc[1]-int(10*s)), (hc[0]+hr+int(15*s), hc[1]+hr+int(40*s))], fill=color, width=int(5*s))
        rb_x, rb_y = hc[0]+int(15*s), hc[1]-hr-int(5*s)
        draw.polygon([(rb_x, rb_y), (rb_x-int(18*s), rb_y-int(15*s)), (rb_x-int(5*s), rb_y+int(5*s))], fill=PINK)
        draw.polygon([(rb_x, rb_y), (rb_x+int(18*s), rb_y-int(15*s)), (rb_x+int(5*s), rb_y+int(5*s))], fill=PINK)
    else:
        draw.arc([hc[0]-hr-int(3*s), hc[1]-hr-int(12*s), hc[0]+hr+int(3*s), hc[1]-int(10*s)], 180, 360, fill=color, width=int(6*s))

    ey = hc[1] - int(8*s)
    el, er = hc[0] - int(18*s), hc[0] + int(18*s)
    es = int(6*s)
    my = hc[1] + int(18*s)

    if expr == "angry":
        draw.ellipse([el-es, ey-es, el+es, ey+es], fill=WHITE)
        draw.ellipse([er-es, ey-es, er+es, ey+es], fill=WHITE)
        draw.line([(el-int(14*s), ey-int(20*s)), (el+int(10*s), ey-int(12*s))], fill=RED, width=int(4*s))
        draw.line([(er+int(14*s), ey-int(20*s)), (er-int(10*s), ey-int(12*s))], fill=RED, width=int(4*s))
        draw.line([(cx-int(15*s), my), (cx+int(15*s), my)], fill=WHITE, width=int(3*s))
        sx, sy = hc[0]+hr+int(10*s), hc[1]-hr+int(5*s)
        draw.line([(sx-int(8*s), sy-int(8*s)), (sx+int(8*s), sy+int(8*s))], fill=RED, width=int(3*s))
        draw.line([(sx+int(8*s), sy-int(8*s)), (sx-int(8*s), sy+int(8*s))], fill=RED, width=int(3*s))
    elif expr == "talking":
        draw.ellipse([el-es, ey-es, el+es, ey+es], fill=WHITE)
        draw.ellipse([er-es, ey-es, er+es, ey+es], fill=WHITE)
        draw.ellipse([cx-int(10*s), my-int(4*s), cx+int(10*s), my+int(10*s)], outline=WHITE, width=int(3*s))
    elif expr == "surprised":
        be = int(10*s)
        draw.ellipse([el-be, ey-be, el+be, ey+be], outline=WHITE, width=int(3*s))
        draw.ellipse([er-be, ey-be, er+be, ey+be], outline=WHITE, width=int(3*s))
        draw.ellipse([cx-int(8*s), my-int(5*s), cx+int(8*s), my+int(8*s)], outline=WHITE, width=int(3*s))
        draw.line([(hc[0], hc[1]-hr-int(30*s)), (hc[0], hc[1]-hr-int(12*s))], fill=YELLOW, width=int(5*s))
    elif expr == "sad":
        draw.ellipse([el-es, ey-es, el+es, ey+es], fill=WHITE)
        draw.ellipse([er-es, ey-es, er+es, ey+es], fill=WHITE)
        draw.arc([(cx-int(12*s), my), (cx+int(12*s), my+int(14*s))], 200, 340, fill=WHITE, width=int(3*s))
        draw.line([(er+int(3*s), ey+es), (er+int(10*s), ey+int(22*s))], fill=BLUE, width=int(3*s))
    elif expr == "happy":
        draw.arc([(el-es-int(4*s), ey-int(10*s)), (el+es+int(4*s), ey+int(4*s))], 200, 340, fill=WHITE, width=int(4*s))
        draw.arc([(er-es-int(4*s), ey-int(10*s)), (er+es+int(4*s), ey+int(4*s))], 200, 340, fill=WHITE, width=int(4*s))
        draw.arc([(cx-int(14*s), my-int(8*s)), (cx+int(14*s), my+int(8*s))], 10, 170, fill=WHITE, width=int(3*s))
    else:
        draw.ellipse([el-es, ey-es, el+es, ey+es], fill=WHITE)
        draw.ellipse([er-es, ey-es, er+es, ey+es], fill=WHITE)
        draw.line([(cx-int(12*s), my), (cx+int(12*s), my)], fill=WHITE, width=int(3*s))

    draw.line([bt, bb], fill=WHITE, width=lw)
    arm_y = bt[1] + int(35*s)
    if expr == "angry":
        draw.line([(cx, arm_y), (cx-int(45*s), arm_y+int(25*s))], fill=WHITE, width=lw)
        draw.line([(cx, arm_y), (cx+int(45*s), arm_y+int(25*s))], fill=WHITE, width=lw)
    elif expr == "surprised":
        draw.line([(cx, arm_y), (cx-al, arm_y-int(50*s))], fill=WHITE, width=lw)
        draw.line([(cx, arm_y), (cx+al, arm_y-int(50*s))], fill=WHITE, width=lw)
    elif expr == "talking":
        draw.line([(cx, arm_y), (cx-al, arm_y+int(40*s))], fill=WHITE, width=lw)
        draw.line([(cx, arm_y), (cx+int(60*s), arm_y-int(30*s))], fill=WHITE, width=lw)
    elif expr == "sad":
        draw.line([(cx, arm_y), (cx-int(40*s), arm_y+int(60*s))], fill=WHITE, width=lw)
        draw.line([(cx, arm_y), (cx+int(40*s), arm_y+int(60*s))], fill=WHITE, width=lw)
    else:
        draw.line([(cx, arm_y), (cx-al, arm_y+int(45*s))], fill=WHITE, width=lw)
        draw.line([(cx, arm_y), (cx+al, arm_y+int(45*s))], fill=WHITE, width=lw)
    draw.line([bb, (cx-int(40*s), cy)], fill=WHITE, width=lw)
    draw.line([bb, (cx+int(40*s), cy)], fill=WHITE, width=lw)


def draw_text_top(draw, text, font, fill=WHITE):
    wrapped = ""
    for line in text.split("\n"):
        while len(line) > 18:
            wrapped += line[:18] + "\n"
            line = line[18:]
        wrapped += line + "\n"
    wrapped = wrapped.strip()
    lines = wrapped.split("\n")
    lh = font.size + 18
    y = 200
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x = W // 2 - tw // 2
        cy = y + i * lh
        for dx in [-3, -2, -1, 0, 1, 2, 3]:
            for dy in [-3, -2, -1, 0, 1, 2, 3]:
                if dx != 0 or dy != 0:
                    draw.text((x + dx, cy + dy), line, font=font, fill=(0, 0, 0))
        draw.text((x, cy), line, font=font, fill=fill)


def guess_expr(sentence):
    if any(w in sentence for w in ["싸웠", "화", "짜증", "한숨"]): return "angry"
    elif any(w in sentence for w in ['"', "라고 했", "말했"]): return "talking"
    elif any(w in sentence for w in ["여러분", "어떠신", "사랑"]): return "happy"
    elif any(w in sentence for w in ["서운", "슬", "울", "힘들"]): return "sad"
    elif any(w in sentence for w in ["놀", "헐", "뭐", "없"]): return "surprised"
    return "neutral"


# ===== 장면 생성 =====

def make_cover(out, topic):
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw_text_top(draw, "저희 오늘 또\n싸웠습니다.", font_title)
    lines = f"- {topic}편 -"
    bbox = draw.textbbox((0, 0), lines, font=font_topic)
    tw = bbox[2] - bbox[0]
    x = W // 2 - tw // 2
    for dx in [-3, -2, -1, 0, 1, 2, 3]:
        for dy in [-3, -2, -1, 0, 1, 2, 3]:
            if dx != 0 or dy != 0:
                draw.text((x + dx, 420 + dy), lines, font=font_topic, fill=(0, 0, 0))
    draw.text((x, 420), lines, font=font_topic, fill=YELLOW)
    draw_stick(draw, 370, 1350, "male", "angry", 1.4)
    draw_stick(draw, 710, 1350, "female", "angry", 1.4)
    img.save(out)

def make_body(out, text, gender, expr):
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw_text_top(draw, text, font_body)
    draw_stick(draw, W // 2, 1350, gender, expr, 1.6)
    img.save(out)

def make_closing(out):
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw_text_top(draw, CLOSING_TEXT, font_closing)
    draw_stick(draw, 380, 1350, "male", "happy", 1.2)
    draw_stick(draw, 700, 1350, "female", "happy", 1.2)
    img.save(out)

def make_question(out, text):
    img = Image.new("RGBA", (W, H), BG)
    draw = ImageDraw.Draw(img)
    draw_text_top(draw, text, font_body)
    q_font = ImageFont.truetype(font_path, 150)
    bbox = draw.textbbox((0, 0), "?", font=q_font)
    tw = bbox[2] - bbox[0]
    draw.text((W // 2 - tw // 2, 750), "?", font=q_font, fill=YELLOW)
    draw_stick(draw, 380, 1400, "male", "surprised", 1.0)
    draw_stick(draw, 700, 1400, "female", "surprised", 1.0)
    img.save(out)


# ===== TTS / FFmpeg =====

def num_to_korean(text):
    time_map = {'1시': '한 시', '2시': '두 시', '3시': '세 시', '4시': '네 시', '5시': '다섯 시',
                '6시': '여섯 시', '7시': '일곱 시', '8시': '여덟 시', '9시': '아홉 시',
                '10시': '열 시', '11시': '열한 시', '12시': '열두 시'}
    for k, v in time_map.items(): text = text.replace(k, v)
    min_map = {'10분': '십 분', '15분': '십오 분', '20분': '이십 분',
               '30분': '삼십 분', '40분': '사십 분', '50분': '오십 분'}
    for k, v in min_map.items(): text = text.replace(k, v)
    char_map = {'1글자': '한 글자', '2글자': '두 글자', '3글자': '세 글자',
                '4글자': '네 글자', '5글자': '다섯 글자'}
    for k, v in char_map.items(): text = text.replace(k, v)
    return text

def gen_tts(text, gender, out):
    if not GCP_TTS_KEY:
        print("  ❌ GCP_TTS_KEY 환경 변수가 없습니다.")
        return False
    text = num_to_korean(text)
    resp = requests.post(TTS_URL, json={
        "input": {"text": text}, "voice": VOICES[gender], "audioConfig": AUDIO_CONFIGS[gender]
    })
    if resp.status_code == 200:
        raw = out + ".raw"
        with open(raw, "wb") as f:
            f.write(base64.b64decode(resp.json()["audioContent"]))
        subprocess.run(["ffmpeg", "-y", "-i", raw, "-ar", str(SAMPLE_RATE), "-ac", "1", out], capture_output=True)
        os.remove(raw)
        return True
    print(f"  ❌ TTS 실패 ({resp.status_code}): {resp.text[:200]}")
    if resp.status_code == 403:
        print("  → GCP TTS API 키가 유효하지 않거나 quota가 초과되었습니다.")
    elif resp.status_code == 400:
        print("  → TTS 요청 데이터가 올바르지 않습니다.")
    return False

def get_dur(p):
    return float(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", p
    ]).decode().strip())

def clip_audio(img, audio, dur, out):
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", img, "-i", audio,
        "-vf", "scale=1080:1920,setsar=1,fade=in:st=0:d=0.3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-r", "25", "-shortest", out
    ], capture_output=True)

def clip_gap(img, gap_dur, out):
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-t", str(gap_dur), "-i", img,
        "-f", "lavfi", "-t", str(gap_dur), "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
        "-vf", "scale=1080:1920,setsar=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "1", "-r", "25", "-shortest", out
    ], capture_output=True)

def clip_silent(img, dur, out):
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-t", str(dur), "-i", img,
        "-f", "lavfi", "-t", str(dur), "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
        "-vf", "scale=1080:1920,setsar=1,fade=in:st=0:d=0.5", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-r", "25", "-shortest", out
    ], capture_output=True)


# ===== 영상 생성 =====

def generate_video(ep):
    print("[3/7] 영상 생성 중...")
    gender = ep["gender"]
    sentences = ep["sentences"]
    question = ep["question"]
    topic = ep["topic"]

    d = WORK_DIR
    if os.path.exists(d): shutil.rmtree(d)
    os.makedirs(d)

    # 첫 문장("저희 오늘 또 싸웠습니다")은 커버에서 처리하므로 본문에서 제외
    body = [s for s in sentences if "저희 오늘 또 싸웠" not in s]

    # TTS
    print("[4/7] TTS 생성 중...")
    audios, durs = [], []
    for i, s in enumerate(body):
        ap = f"{d}/s{i:02d}.mp3"
        if not gen_tts(s, gender, ap): return None
        durs.append(get_dur(ap))
        audios.append(ap)
    qa = f"{d}/q.mp3"
    if not gen_tts(question, gender, qa): return None
    qd = get_dur(qa)

    # 이미지
    print("[5/7] 이미지 생성 중...")
    cp = f"{d}/cover.png"
    make_cover(cp, topic)
    bimgs = []
    for i, s in enumerate(body):
        ip = f"{d}/b{i:02d}.png"
        make_body(ip, s, gender, guess_expr(s))
        bimgs.append(ip)
    qi = f"{d}/q.png"
    make_question(qi, question)
    ci = f"{d}/closing.png"
    make_closing(ci)

    # 커버 음성
    cover_voice = os.path.join(ASSETS_DIR, "cover_duo_pause.mp3")

    # 클립 조립
    print("[6/7] FFmpeg 합성 중...")
    clips = []
    cc = f"{d}/cc.mp4"
    clip_audio(cp, cover_voice, COVER_DURATION, cc)
    clips.append(cc)

    cg = f"{d}/g_cover.mp4"
    clip_gap(cp, 1.5, cg)
    clips.append(cg)

    GAP = 1.2
    for i, (img, audio, dur) in enumerate(zip(bimgs, audios, durs)):
        c = f"{d}/c{i:02d}.mp4"
        clip_audio(img, audio, dur, c)
        clips.append(c)
        g = f"{d}/g{i:02d}.mp4"
        clip_gap(img, GAP, g)
        clips.append(g)

    qc = f"{d}/cq.mp4"
    clip_audio(qi, qa, qd, qc)
    clips.append(qc)

    ec = f"{d}/ce.mp4"
    clip_silent(ci, CLOSING_DURATION, ec)
    clips.append(ec)

    cl = f"{d}/concat.txt"
    with open(cl, "w") as f:
        for c in clips: f.write(f"file '{c}'\n")

    out = f"{d}/final.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", cl,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "1", out
    ], capture_output=True)

    if os.path.exists(out):
        t = get_dur(out)
        print(f"  영상 완료: {t:.1f}초")
        return out
    print("  영상 생성 실패!")
    return None


# ===== YouTube 업로드 =====

def upload_youtube(video_path, title, description):
    print("[7/7] YouTube 업로드 중...")

    # 환경 변수 존재 여부 확인
    missing = []
    if not YT_CLIENT_ID: missing.append("YT_CLIENT_ID")
    if not YT_CLIENT_SECRET: missing.append("YT_CLIENT_SECRET")
    if not YT_REFRESH_TOKEN: missing.append("YT_REFRESH_TOKEN")
    if missing:
        print(f"  ❌ YouTube 환경 변수 누락: {', '.join(missing)}")
        return None

    # 토큰 갱신
    token_resp = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": YT_CLIENT_ID, "client_secret": YT_CLIENT_SECRET,
        "refresh_token": YT_REFRESH_TOKEN, "grant_type": "refresh_token"
    })
    if token_resp.status_code != 200:
        err = token_resp.json() if token_resp.headers.get("content-type", "").startswith("application/json") else {"error": token_resp.text[:300]}
        error_desc = err.get("error_description", err.get("error", "unknown"))
        print(f"  ❌ YouTube 토큰 갱신 실패 ({token_resp.status_code}): {error_desc}")
        if "invalid_grant" in str(err):
            print("  → Refresh Token이 만료되었거나 유효하지 않습니다.")
            print("  → Google Cloud Console에서 OAuth 토큰을 새로 생성하세요.")
        return None

    access_token = token_resp.json()["access_token"]
    print("  YouTube 토큰 갱신 성공")
    filesize = os.path.getsize(video_path)

    init_resp = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "X-Upload-Content-Type": "video/mp4",
            "X-Upload-Content-Length": str(filesize)
        },
        json={
            "snippet": {
                "title": title, "description": description,
                "tags": ["그날의남녀", "커플싸움", "쇼츠", "shorts", "커플"],
                "categoryId": "22"
            },
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False}
        }
    )
    if init_resp.status_code != 200:
        print(f"  ❌ 업로드 초기화 실패 ({init_resp.status_code}): {init_resp.text[:300]}")
        return None

    upload_url = init_resp.headers.get("Location")
    print(f"  영상 업로드 중... ({filesize / 1024 / 1024:.1f}MB)")
    with open(video_path, "rb") as f:
        upload_resp = requests.put(upload_url, headers={"Content-Type": "video/mp4"}, data=f)

    if upload_resp.status_code in (200, 201):
        vid = upload_resp.json().get("id")
        print(f"  ✅ YouTube 업로드 완료: https://youtube.com/shorts/{vid}")
        return vid
    else:
        print(f"  ❌ YouTube 업로드 실패 ({upload_resp.status_code}): {upload_resp.text[:300]}")
        return None


# ===== Threads 포스팅 =====

def post_threads(text):
    print("  [Threads] 포스팅 중...")
    if not THREADS_TOKEN:
        print("  ❌ THREADS_TOKEN 환경 변수가 없습니다. 건너뜀.")
        return False

    resp = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads",
        params={"media_type": "TEXT", "text": text, "access_token": THREADS_TOKEN}
    )
    if resp.status_code != 200:
        err = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"error": resp.text[:300]}
        print(f"  ❌ 컨테이너 생성 실패 ({resp.status_code}): {err}")
        if "OAuthException" in str(err) or "expired" in str(err).lower() or resp.status_code == 401:
            print("  → Threads 토큰이 만료되었거나 유효하지 않습니다.")
            print("  → Meta for Developers에서 토큰을 새로 생성하세요.")
        elif "Permission" in str(err) or resp.status_code == 403:
            print("  → threads_basic 또는 threads_content_write 권한이 필요합니다.")
        return False

    creation_id = resp.json().get("id")
    print(f"  컨테이너 생성됨 (ID: {creation_id})")
    time.sleep(3)

    pub_resp = requests.post(
        f"https://graph.threads.net/v1.0/{THREADS_USER_ID}/threads_publish",
        params={"creation_id": creation_id, "access_token": THREADS_TOKEN}
    )
    if pub_resp.status_code == 200:
        print(f"  ✅ Threads 게시 완료")
        return True
    else:
        print(f"  ❌ Threads 게시 실패 ({pub_resp.status_code}): {pub_resp.text[:300]}")
        return False


# ===== Google Sheets 상태 업데이트 =====

def get_sheets_service():
    """Google Sheets API 서비스 생성"""
    if not GOOGLE_SERVICE_ACCOUNT:
        print("  GOOGLE_SERVICE_ACCOUNT 없음. Sheets 업데이트 건너뜀.")
        return None
    creds_info = json.loads(GOOGLE_SERVICE_ACCOUNT)
    creds = Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return build("sheets", "v4", credentials=creds)


def update_sheet_status(row_num, yt_success, threads_success):
    """Sheets에 상태 업데이트 + 셀 색상 변경"""
    service = get_sheets_service()
    if not service:
        return

    sheet = service.spreadsheets()

    # Status 컬럼(A열)을 "완료"로 변경
    if yt_success:
        sheet.values().update(
            spreadsheetId=SHEET_ID,
            range=f"A{row_num}",
            valueInputOption="RAW",
            body={"values": [["완료"]]}
        ).execute()

        # A열 배경색 초록색
        sheet.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{
                "repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": row_num - 1, "endRowIndex": row_num,
                              "startColumnIndex": 0, "endColumnIndex": 1},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 0.56, "green": 0.93, "blue": 0.56}}},
                    "fields": "userEnteredFormat.backgroundColor"
                }
            }]}
        ).execute()

    # Threads 글감 컬럼(G열) 배경색 분홍색
    if threads_success:
        sheet.batchUpdate(
            spreadsheetId=SHEET_ID,
            body={"requests": [{
                "repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": row_num - 1, "endRowIndex": row_num,
                              "startColumnIndex": 6, "endColumnIndex": 7},
                    "cell": {"userEnteredFormat": {"backgroundColor": {"red": 1.0, "green": 0.75, "blue": 0.8}}},
                    "fields": "userEnteredFormat.backgroundColor"
                }
            }]}
        ).execute()

    print(f"  Sheets 상태 업데이트 완료 (행 {row_num})")


# ===== 메인 =====

if __name__ == "__main__":
    print("=" * 50)
    print("그날의 남녀 - 자동화 파이프라인")
    print("=" * 50)

    # 1. Google Sheets에서 다음 에피소드 가져오기
    ep = fetch_next_episode()
    if not ep:
        print("\n⚠ 처리할 에피소드가 없습니다. 다음 실행을 기다립니다.")
        sys.exit(1)

    # 2. 메타데이터 구성
    title = f"저희 오늘 또 싸웠습니다. - {ep['topic']}편 - | 그날의 남녀 {ep['ep']}"
    description = f"{ep['question']}\n\n#그날의남녀 #커플싸움 #{ep['topic']} #쇼츠 #shorts"

    # Threads 텍스트: 대본 전문 + 마무리 질문
    threads_text = f"{ep['script']}\n\n{ep['question']}"

    # 3. 영상 생성
    video_path = generate_video(ep)
    if not video_path:
        sys.exit(1)

    # 4. YouTube 업로드
    yt_id = upload_youtube(video_path, title, description)

    # 5. Threads 포스팅
    threads_ok = post_threads(threads_text)

    # 6. Google Sheets 상태 업데이트
    update_sheet_status(ep["row_num"], yt_id is not None, threads_ok)

    # 완료 요약
    print(f"\n{'='*50}")
    print(f"결과 요약 - {ep['ep']} - {ep['topic']}편")
    print(f"{'='*50}")
    print(f"  영상 생성: ✅")
    print(f"  YouTube: {'✅ https://youtube.com/shorts/' + yt_id if yt_id else '❌ 실패'}")
    print(f"  Threads: {'✅' if threads_ok else '❌ 실패 (토큰 확인 필요)'}")
    print(f"{'='*50}")

    if not yt_id:
        sys.exit(1)
