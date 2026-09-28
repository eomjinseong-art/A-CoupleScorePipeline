"""
그날의 남녀 - 일일 자동화 파이프라인 (GitHub Actions용)
전체 흐름: Google Sheets에서 대본 읽기 → 영상 생성 → YouTube 업로드 → Threads 포스팅 → 블로그 발행
"""
import os, json, time, base64, subprocess, sys, shutil, csv, io
import requests
from PIL import ImageDraw

from shorts_format import (
    AUDIO_CONFIGS,
    VOICES,
    YOUTUBE_TAGS,
    build_description,
    build_lines,
    build_threads_text,
    build_title,
    contains_english_learning,
    find_font,
    prepare_episode,
    render_card,
    youtube_upload_enabled,
)

# === 환경 변수 ===
GCP_TTS_KEY = os.environ.get("GCP_TTS_KEY")
YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID")
YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")
THREADS_TOKEN = os.environ.get("THREADS_TOKEN")
THREADS_USER_ID = "27227055083638713"
GOOGLE_SERVICE_ACCOUNT = os.environ.get("GOOGLE_SERVICE_ACCOUNT")  # JSON string

WORK_DIR = "/tmp/couple_render"

# Google Sheets (공개 읽기)
SHEET_ID = "1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20"
SHEET_CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid=0"

TTS_URL = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GCP_TTS_KEY}"
SAMPLE_RATE = 44100
W, H = 1080, 1920
BG = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (255, 215, 0)
PINK = (255, 140, 170)
BLUE = (130, 170, 255)
RED = (255, 90, 90)


# ===== Google Sheets 읽기 =====

def fetch_next_episode():
    """Google Sheets에서 Status='대기'인 첫 번째 에피소드를 가져옴.

    훅·투표·심리는 시트 원문을 바꾸지 않고 생성 시점에 붙인다.
    """
    print("[1/7] Google Sheets에서 대본 읽기...")
    resp = requests.get(SHEET_CSV_URL)
    if resp.status_code != 200:
        print(f"  Sheets 접근 실패: {resp.status_code}")
        return None

    content = resp.content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    print(f"  CSV 헤더: {reader.fieldnames}")
    for row_idx, row in enumerate(reader, start=2):
        if row.get("Status", "").strip() == "대기":
            row = dict(row)
            row["row_num"] = row_idx
            ep = prepare_episode(row)
            print(f"  대상: {ep['ep']} ({ep['speaker']}) - {ep['topic']}편 (행 {row_idx})")
            print(f"  훅: {ep['hook']}")
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


def paint_shot(shot, font_path):
    """자막은 안전 영역, 막대기는 그 아래."""
    img = render_card(shot, font_path).convert("RGBA")
    draw = ImageDraw.Draw(img)
    # 막대기는 자막 안전 영역(y<=1360) 아래, 우측 버튼 왼쪽에 둔다.
    if shot.stick == "both":
        draw_stick(draw, 280, 1780, "male", shot.expr, 0.85)
        draw_stick(draw, 600, 1780, "female", shot.expr, 0.85)
    else:
        draw_stick(draw, 440, 1780, shot.stick, shot.expr, 0.9)
    return img


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
    """음성이 짧아도 dur초 동안 같은 화면을 유지한다."""
    subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", img, "-i", audio,
        "-filter_complex", f"[1:a]apad=whole_dur={dur}[a]",
        "-map", "0:v", "-map", "[a]",
        "-t", str(dur),
        "-vf", "scale=1080:1920,setsar=1,fade=in:st=0:d=0.15",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-r", "25", out
    ], capture_output=True)

# ===== 영상 생성 =====

def _silent_audio(path, dur):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-t", str(dur),
        "-i", f"anullsrc=r={SAMPLE_RATE}:cl=mono",
        "-c:a", "libmp3lame", path
    ], capture_output=True)


def generate_video(ep, with_tts=True, work_dir=None):
    """첫 장면은 갈등 훅. 남녀 목소리를 나누고, 끝은 심리 한 줄과 편 투표."""
    print("[3/7] 영상 생성 중...")
    lines = build_lines(ep)
    d = work_dir or WORK_DIR
    if os.path.exists(d):
        shutil.rmtree(d)
    os.makedirs(d)
    font_path = find_font()

    print("[4/7] TTS 생성 중..." if with_tts else "[4/7] TTS 생략 (무음 미리보기)")
    audios = []
    durations = []
    for i, line in enumerate(lines):
        ap = f"{d}/s{i:02d}.mp3"
        if with_tts:
            if not gen_tts(line.spoken, line.voice, ap):
                return None
            spoken = get_dur(ap)
        else:
            spoken = max(line.min_duration, 1.1)
            _silent_audio(ap, spoken)
        dur = max(line.min_duration, spoken)
        audios.append(ap)
        durations.append(dur)

    print("[5/7] 이미지 생성 중...")
    images = []
    for i, line in enumerate(lines):
        img = paint_shot(line, font_path)
        ip = f"{d}/f{i:02d}.png"
        img.save(ip)
        images.append(ip)

    print("[6/7] FFmpeg 합성 중...")
    clips = []
    for i, (img, audio, dur) in enumerate(zip(images, audios, durations)):
        clip = f"{d}/c{i:02d}.mp4"
        clip_audio(img, audio, dur, clip)
        if not os.path.exists(clip):
            print(f"  클립 생성 실패: {clip}")
            return None
        clips.append(clip)

    listing = f"{d}/concat.txt"
    with open(listing, "w") as f:
        for clip in clips:
            f.write(f"file '{clip}'\n")

    out = f"{d}/final.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", listing,
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ar", str(SAMPLE_RATE), "-ac", "1", out
    ], capture_output=True)

    if os.path.exists(out):
        print(f"  영상 완료: {get_dur(out):.1f}초, 첫 장면={lines[0].role}")
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
                "tags": list(YOUTUBE_TAGS),
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
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

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

def run(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in argv
    print("=" * 50)
    print("그날의 남녀 - 자동화 파이프라인")
    print("=" * 50)

    ep = fetch_next_episode()
    if not ep:
        print("\n⚠ 처리할 에피소드가 없습니다. 다음 실행을 기다립니다.")
        return 1

    title = build_title(ep["hook"], ep["ep"])
    description = build_description(ep)
    threads_text = build_threads_text(ep)
    upload_yt = youtube_upload_enabled() and not dry_run
    print(f"  제목: {title}")
    print(f"  YouTube 업로드: {'예' if upload_yt else '생략'}")
    if any(contains_english_learning(tag) for tag in YOUTUBE_TAGS):
        print("  ❌ 태그에 영어학습 키워드가 있습니다.")
        return 1

    yt_id = None
    if dry_run or upload_yt:
        video_path = generate_video(ep, with_tts=not dry_run)
        if not video_path:
            return 1
        if dry_run:
            print(f"  미리보기 영상: {video_path}")
            print("  업로드·시트 기록은 하지 않습니다.")
            return 0
        yt_id = upload_youtube(video_path, title, description)
    else:
        print("[3/7] YouTube를 올리지 않는 실행이라 영상 생성도 건너뜁니다.")

    # 5. Threads 포스팅
    threads_ok = post_threads(threads_text)

    # 6. 블로그 콘텐츠 생성 + 발행
    blog_ok = False
    try:
        from generate_blog_content import generate_blog_post, get_or_create_blog_sheet, find_next_row, load_client as load_blog_client
        from publish_blogger import get_blogger_token, get_blog_id, publish_to_blogger, get_sheets_client

        print("[8/9] 블로그 콘텐츠 생성 중...")
        blog_data = generate_blog_post(ep)
        if blog_data:
            # 시트에 기록
            gc = load_blog_client()
            ws = get_or_create_blog_sheet(gc)
            next_row = find_next_row(ws)
            ep_num = ep["ep"].replace("EP.", "").replace("ep.", "").strip()
            row_data = [
                "대기", ep_num, blog_data["title"], blog_data["html"],
                ",".join(blog_data["tags"]),
                f"https://youtube.com/shorts/{yt_id}" if yt_id else "",
                "", ""
            ]
            ws.update(range_name=f"A{next_row}:H{next_row}", values=[row_data])
            print(f"  블로그 콘텐츠 기록 완료: {blog_data['title']}")

            # Blogger 발행
            print("[9/9] 블로거 발행 중...")
            token = get_blogger_token()
            if token:
                blog_id, _ = get_blog_id(token)
                if blog_id:
                    post_url = publish_to_blogger(
                        token, blog_id, blog_data["title"],
                        blog_data["html"], ",".join(blog_data["tags"])
                    )
                    if post_url:
                        blog_ok = True
                        from datetime import datetime
                        ws.update(range_name=f"A{next_row}:H{next_row}", values=[[
                            "발행완료", ep_num, blog_data["title"], blog_data["html"],
                            ",".join(blog_data["tags"]),
                            f"https://youtube.com/shorts/{yt_id}" if yt_id else "",
                            post_url, datetime.now().strftime("%Y-%m-%d %H:%M")
                        ]])
                else:
                    print("  ❌ 블로그 ID 조회 실패")
            else:
                print("  ❌ Blogger 토큰 갱신 실패")
    except Exception as e:
        print(f"  ❌ 블로그 발행 중 오류: {e}")

    # 09:00 실행은 영상을 올리지 않으므로, Threads나 블로그가 되면 행을 소진한다.
    # 21:00 실행은 영상이 올라간 뒤에만 완료로 바꾼다.
    consumed = (yt_id is not None) if upload_yt else (threads_ok or blog_ok)
    update_sheet_status(ep["row_num"], consumed, threads_ok)

    # 완료 요약
    print(f"\n{'='*50}")
    print(f"결과 요약 - {ep['ep']} - {ep['topic']}편")
    print(f"{'='*50}")
    print(f"  영상 생성: {'✅' if upload_yt else '⏭ YouTube 없는 실행'}")
    if not upload_yt:
        yt_line = "⏭ 생략 (21:00 KST에 1편)"
    elif yt_id:
        yt_line = "✅ https://youtube.com/shorts/" + yt_id
    else:
        yt_line = "❌ 실패"
    print(f"  YouTube: {yt_line}")
    print(f"  Threads: {'✅' if threads_ok else '❌ 실패 (토큰 확인 필요)'}")
    print(f"  블로그: {'✅' if blog_ok else '❌ 실패 (토큰/API 확인 필요)'}")
    print(f"{'='*50}")

    if upload_yt and not yt_id:
        return 1
    if not upload_yt and not (threads_ok or blog_ok):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(run())
