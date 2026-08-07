# 그날의 남녀 - 자동화 파이프라인

매일 자동으로 Google Sheets에서 대본을 읽어 영상을 생성하고, YouTube Shorts 업로드 + Threads 포스팅을 수행합니다.

## 작동 흐름
1. Google Sheets에서 Status="대기"인 첫 번째 에피소드 선택
2. Google Cloud TTS로 음성 생성
3. Python(Pillow)으로 막대기 캐릭터 + 텍스트 이미지 생성
4. FFmpeg로 영상 합성
5. YouTube Shorts API로 공개 업로드
6. Threads API로 대본 텍스트 포스팅
7. (Sheets에서 Status를 "완료"로 변경하면 다음 에피소드로 넘어감)

## 에피소드 관리
- Google Sheets: https://docs.google.com/spreadsheets/d/1l7niiK9RbZwo_x0PI6T2vCqjIKn9c_gvxVrrKjwyo20
- Status 컬럼: "대기" → 업로드 대상, "완료" → 건너뜀
- 대본 수정: Sheets에서 직접 수정하면 다음 실행 시 반영

## 실행 시간
- 매일 한국 시간 오전 8시, 오후 1시, 오후 7시 (하루 3개)
- GitHub Actions에서 "Run workflow" 버튼으로 수동 실행 가능

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
