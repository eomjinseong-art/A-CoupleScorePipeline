# 그날의 남녀 - 자동화 파이프라인

매일 자동으로 `data/reconcile_episodes.json`에서 대본을 읽어 영상을 생성하고, YouTube Shorts 업로드 + Threads 포스팅 + 블로그 발행을 수행합니다.

## 작동 흐름
1. 예약 슬롯에 해당하는 `data/reconcile_episodes.json` 항목을 고른다. 큐가 비면 로그만 남기고 건너뛴다
2. 시트 원문은 바꾸지 않는다. 큐 회차는 시트 Status도 바꾸지 않는다
3. 첫 장면은 갈등 한 줄. 남자·여자 목소리를 나누고, 끝은 화해 한 줄과 질문
4. edge-tts로 음성 생성 (실패 시 gTTS). 워크플로는 유료 TTS 키를 넣지 않는다
5. 자막은 쇼츠 안전 영역(상단 검색, 우측 버튼, 하단 제목 밖)에 그림
6. YouTube는 21:00 KST에만 1편. 제목은 갈등으로 시작하고 채널명·EP는 끝에 둠
7. Threads·블로그는 09:00과 21:00 KST. 대본 전문은 유지
8. 큐 회차는 슬롯 시각으로만 진행한다. 시트 행을 쓰는 수동 실행(`RECONCILE_QUEUE=off`)만 Status를 "완료"로 바꾼다

## 2026-10 콘셉트 변경: '우리 오늘 화해했어요'
- **글감 순서**: 예약 실행은 `data/reconcile_episodes.json`만 사용한다. 슬롯이 큐 길이를 넘으면 로그만 남기고 건너뛴다(OpenAI 호출 없음). 시트 `대기` 행은 `RECONCILE_QUEUE=off`일 때만 읽는다. 큐 계산은 `docs/CONTENT_QUEUE.md`.
- 큐 회차는 저장소 상태 커밋 없이 **예약 슬롯 번호**로 정한다: 2026-10-16 오전(09:00 크론)=슬롯 0, 이후 하루 2슬롯(UTC 날짜 기준 오전/저녁). 크론이 몇 시간 밀려도 같은 슬롯.
- 큐 회차는 시트 Status를 바꾸지 않는다(시트 원본 보존). Threads는 최근 글과 같으면 다시 올리지 않는다.
- 형식: 싸움 훅 2~3줄(`fight`) → 화해 장면(`makeup`: 사과 한마디·작은 선물·산책·그날 입은 옷) → 따뜻한 한 줄(`warm`) → `여러분은 어떻게 화해해요?` 질문. `shop: true`인 회차(3~4편에 1편)만 끝에 아브아르(AVOIR) 링크 한 줄.
- Shorts: 훅(갈등) → 싸움 → 화해(웃는 얼굴) → '오늘의 화해' 카드 → 질문 카드. 제목 `훅 | 그날의남녀 화해 EP.NN`, 태그·설명도 화해 콘셉트.
- 블로그: 예약 실행은 큐의 싸움·화해·따뜻한 한 줄을 HTML로 옮긴다. LLM 초안은 수동 스크립트만 만든다.
- 큐 항목 예시:
```json
{"id": "R065", "speaker": "여자", "topic": "주제", "hook": "첫 2초 갈등 한 줄",
 "fight": "저희 오늘 싸웠어요.\n상황 한 줄.\n대사 한 줄.", "makeup": "화해 장면 3~5줄(한 줄 = 한 문장)",
 "warm": "따뜻한 한 줄", "question": "여러분은 어떻게 화해해요?", "outfit": "크림 니트", "shop": false}
```
- 확인: `python main.py --dry-run --index 0` (업로드·시트 기록 없음, 무음 미리보기 영상), `python -m unittest discover -s tests`

## 에피소드 관리
- Google Sheets: https://docs.google.com/spreadsheets/d/1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20
- Status 컬럼: "대기" → 업로드 대상, "완료" → 건너뜀
- 대본 수정: `data/reconcile_episodes.json`에 회차를 추가하면 해당 슬롯부터 반영된다. 계산은 `docs/CONTENT_QUEUE.md`

## 실행 시간
- 매일 한국 시간 09:00: Threads + 블로그 (YouTube 없음)
- 매일 한국 시간 21:00: YouTube Short 1편 + Threads + 블로그
- 예약 실행은 LLM으로 대본을 만들지 않는다. 큐는 `data/reconcile_episodes.json`에 직접 채운다. 블로그 HTML도 그 큐를 템플릿에 넣어 만든다. 수동 생성만 `python generate_episodes.py`이며, 그 키는 Actions에 넣지 않는다.
- Threads 장기 토큰은 매주 월요일 `.github/workflows/refresh-token.yml`이 연장하고 `THREADS_TOKEN` 시크릿에 다시 쓴다. 실패하면 이슈 `Threads 토큰 갱신 실패`.
- YouTube·Blogger 갱신이 `invalid_grant`이면 이슈 `구글 로그인 다시 필요`. 큐가 14일 미만이면 이슈 `글감 부족`.
- N01–N20 원작 주제는 없을 때만 시트 맨 아래에 대기 행으로 추가 (기존 셀은 수정하지 않음)
- 수동 실행은 기본으로 YouTube를 올리지 않음. "upload_youtube"를 켜면 올림
- 업로드 없이 확인: `python main.py --dry-run`, 추가될 주제 확인: `python generate_episodes.py --plan`

## GitHub Secrets 설정

| Secret | 값 |
|--------|-----|
| `YT_CLIENT_ID` | YouTube OAuth Client ID |
| `YT_CLIENT_SECRET` | YouTube OAuth Client Secret |
| `YT_REFRESH_TOKEN` | YouTube Refresh Token |
| `THREADS_TOKEN` | Threads Access Token (주간 워크플로가 갱신) |
| `GH_PAT` 또는 `SECRETS_WRITE_TOKEN` | 시크릿 쓰기 PAT. 갱신 워크플로는 `GH_PAT`를 먼저 쓰고, 없으면 `SECRETS_WRITE_TOKEN`을 쓴다 |

## 파일 구조
```
couple_pipeline/
├── main.py                          # 메인 파이프라인 (큐 읽기 + 영상 생성 + 업로드)
├── data/reconcile_episodes.json     # 예약 실행이 소비하는 화해편 큐
├── docs/CONTENT_QUEUE.md            # 큐 스키마, 슬롯, 남은 일수
├── assets/
│   └── cover_duo_pause.mp3          # 커버 남녀 동시 음성 (고정)
├── .github/workflows/upload.yml     # GitHub Actions 크론
├── .github/workflows/refresh-token.yml
├── .github/workflows/batch_blog.yml # 큐 템플릿 일괄 발행 (LLM 없음)
├── scripts/refresh_token.py
├── scripts/check_queue.py
└── README.md
```

## 비용
- GitHub Actions: 무료 (월 2,000분)
- 음성: edge-tts 기본(무료). 실패 시 gTTS. Actions에는 유료 TTS 키를 넣지 않는다
- YouTube/Threads/Blogger API: 무료
- **예약 파이프라인은 OpenAI로 대본을 만들지 않음**
