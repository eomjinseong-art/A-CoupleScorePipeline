# 콘텐츠 큐

예약 실행(`.github/workflows/upload.yml`, 매일 09:00 / 21:00 KST)은 LLM을 호출하지 않고 아래 큐만 소비한다. `generate_episodes.py`는 워크플로에서 실행하지 않는다.

## 경로

| 용도 | 경로 |
| --- | --- |
| 예약 실행이 읽는 유일한 큐 | `data/reconcile_episodes.json` |

다른 커밋된 대본 큐는 없다. `data/topics_n01_n20.csv`와 Google Sheets의 `대기` 행은 예약 경로에서 읽지 않는다. 시트 행은 환경변수 `RECONCILE_QUEUE=off`로 수동 실행할 때만 사용한다.

수동으로 대본을 만들 때만 `python generate_episodes.py`를 쓴다. `GEMINI_API_KEY`가 있으면 Gemini OpenAI 호환 엔드포인트와 모델 `gemini-2.5-flash`를 쓰고, 그 키가 없을 때만 `OPENAI_API_KEY`를 쓴다. 블로그 수동 스크립트(`generate_blog_content.py`, `batch_blog_publish.py`)도 같은 순서다. 예약 실행의 블로그 HTML은 큐 원문으로 만들며 LLM을 호출하지 않는다.

## 항목 스키마

배열의 한 원소가 한 회차다. 슬롯은 `id` 숫자가 아니라 **파일에 적힌 순서(0부터)** 다.

필수: `id`, `speaker`, `topic`, `hook`, `fight`, `makeup`, `warm`, `question`

선택: `outfit`(문자열), `shop`(boolean). `shop: true`는 대략 3~4편에 1편이며, 그때만 아브아르 링크를 붙인다.

- `speaker`: `"남자"` 또는 `"여자"`
- `fight`, `makeup`: 한 줄이 한 문장. 줄바꿈으로 나눈다.
- `question`: `화해`가 들어 있는 질문
- `id`: `R` + 번호. 예: `R001`

```json
{
  "id": "R001",
  "speaker": "여자",
  "topic": "답장 3시간",
  "hook": "카톡 답장 3시간 만에 온 게 \"ㅇㅇ\" 두 글자",
  "fight": "저희 오늘 크게 싸웠어요.\n세 시간 만에 온 답장이 \"ㅇㅇ\" 두 글자였거든요.\n\"나만 기다렸어?\" 한마디에 전화가 뚝 끊겼어요.",
  "makeup": "그런데 저녁에 문 앞에 붕어빵 한 봉지가 걸려 있었어요.\n쪽지에는 \"회의 끝나고 바로 왔어. 미안해.\"라고 적혀 있었고요.\n저는 크림색 니트를 급하게 걸치고 내려갔어요.\n그 사람이 \"그 옷 입으니까 화가 다 풀린 사람 같아.\"라고 해서 결국 웃었어요.",
  "warm": "서운함은 말로 풀고, 마음은 따뜻한 걸로 채우면 돼요.",
  "question": "여러분은 어떻게 화해해요?",
  "outfit": "크림 니트",
  "shop": false
}
```

## 소비를 어떻게 세는가

큐 JSON과 시트에는 소비 플래그를 쓰지 않는다. 회차는 UTC 시각으로 정한 슬롯 번호다.

- 슬롯 0 = 2026-10-16 09:00 KST (크론 UTC 00:00)
- 하루 2슬롯. UTC 00:00–11:59 = 오전(KST 09:00), UTC 12:00–23:59 = 저녁(KST 21:00)
- `slot = (utc_date - 2026-10-16).days * 2 + (1 if utc_hour >= 12 else 0)`
- 배열 인덱스 `slot`이 그 반나절의 발행분이다
- `index < slot` 은 이미 지난 슬롯이다
- 크론이 몇 시간 늦어도 같은 UTC 날짜, 같은 반나절이면 슬롯이 같다
- `slot < 0` 이거나 `slot >= len(queue)` 이면 큐가 비어 있다. 로그 `큐가 비었습니다. 슬롯 N / M개. OpenAI를 호출하지 않고 이번 실행을 건너뜁니다.` 를 남기고 종료 코드 0으로 끝낸다. 시트로 넘어가지 않고 OpenAI도 호출하지 않는다

## 남은 일수

현재 슬롯의 항목을 남은 편에 포함한다. 하루 2편이다.

```
remaining_items = max(0, len(queue) - max(slot, 0))
remaining_days = remaining_items / 2
```

```bash
python -c "import reconcile; print(reconcile.queue_report())"
```

`queue_report()` 키: `path`, `total`, `slot`, `current_id`, `remaining_items`, `remaining_days`, `slots_per_day`, `start`.

09:00 KST 크론이 이미 성공했다면 이번 슬롯은 나간 것이다. 그때는 남은 편에서 1을 빼고, 남은 날에서 0.5를 뺀다. 아래 스냅샷은 크론 성공 여부와 상관없이 시계만으로 계산한 값이다.

## 스냅샷 (2026-11-02 03:05 UTC)

| 항목 | 값 |
| --- | --- |
| 전체 | 104개 (R001–R104, 인덱스 0–103) |
| 슬롯 | 34 (2026-11-02 오전, 현재 항목 R035) |
| 지난 슬롯 | 34개 (R001–R034, 인덱스 0–33) |
| 남은 편 (현재 슬롯 포함) | 70개 |
| 남은 날 (현재 슬롯 포함) | 35일 |
| 마지막 슬롯 | 2026-12-06 21:00 KST (R104, 인덱스 103) |

같은 날 21:00 KST(UTC 12:00)에는 슬롯 35, 현재 항목 R036, 남은 편 69개, 남은 날 34.5일이다. 09:00 KST 크론이 이미 R035를 올렸다면 다음 발행은 2026-11-02 21:00 KST이고, 그때 기준으로는 69개 / 34.5일이 남아 있다.
