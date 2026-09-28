# 그날의 남녀 - 자동화 파이프라인

매일 자동으로 Google Sheets에서 대본을 읽어 영상을 생성하고, YouTube Shorts 업로드 + Threads 포스팅을 수행합니다.

## 작동 흐름
1. Google Sheets에서 Status="대기"인 첫 번째 에피소드 선택
2. 시트 원문은 그대로 두고, 훅·심리·남녀 편 질문은 생성 시점에 붙임 (`data/ep078_107_overrides.csv`)
3. 첫 장면은 갈등 한 줄. 남자·여자 목소리를 나누고, 끝은 심리 한 줄과 편 투표
4. Google Cloud TTS로 음성 생성
5. 자막은 쇼츠 안전 영역(상단 검색, 우측 버튼, 하단 제목 밖)에 그림
6. YouTube는 21:00 KST에만 1편. 제목은 갈등으로 시작하고 채널명·EP는 끝에 둠
7. Threads·블로그는 09:00과 21:00 KST. 대본 전문은 유지
8. 업로드가 끝나면 해당 행 Status를 "완료"로 변경. 09:00에는 Threads 또는 블로그가 되면 완료로 바꿔 같은 행을 저녁에 다시 쓰지 않음

## 에피소드 관리
- Google Sheets: https://docs.google.com/spreadsheets/d/1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20
- Status 컬럼: "대기" → 업로드 대상, "완료" → 건너뜀
- 대본 수정: Sheets에서 직접 수정하면 다음 실행 시 반영

## 실행 시간
- 매일 한국 시간 09:00: Threads + 블로그 (YouTube 없음)
- 매일 한국 시간 21:00: YouTube Short 1편 + Threads + 블로그
- 대기가 30개 이하면 Claude가 편이 갈리는 주제 30개를 추가
- N01–N20 원작 주제는 없을 때만 시트 맨 아래에 대기 행으로 추가 (기존 셀은 수정하지 않음)
- 수동 실행은 기본으로 YouTube를 올리지 않음. "upload_youtube"를 켜면 올림
- 업로드 없이 확인: `python main.py --dry-run`, 추가될 주제 확인: `python generate_episodes.py --plan`

## GitHub Secrets 설정

| Secret | 값 |
|--------|-----|
| `GCP_TTS_KEY` | Google Cloud TTS API 키 |
| `YT_CLIENT_ID` | YouTube OAuth Client ID |
| `YT_CLIENT_SECRET` | YouTube OAuth Client Secret |
| `YT_REFRESH_TOKEN` | YouTube Refresh Token |
| `THREADS_TOKEN` | Threads Access Token |

## 파일 구조
```
couple_pipeline/
├── main.py                          # 메인 파이프라인 (Sheets 읽기 + 영상 생성 + 업로드)
├── assets/
│   └── cover_duo_pause.mp3          # 커버 남녀 동시 음성 (고정)
├── .github/workflows/upload.yml     # GitHub Actions 크론
└── README.md
```

## 비용
- GitHub Actions: 무료 (월 2,000분)
- Google Cloud TTS: 무료 (월 100만 글자)
- YouTube/Threads API: 무료
- **총 월간 비용: $0**
