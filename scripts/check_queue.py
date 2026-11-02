#!/usr/bin/env python3
"""남은 화해 큐가 14일 미만이면 이슈 '글감 부족'을 열거나 본문을 갱신한다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reconcile import queue_report  # noqa: E402
from scripts.github_issues import LOW_QUEUE_ISSUE, close_issue, upsert_issue  # noqa: E402

LOW_QUEUE_DAYS = 14


def _body(report):
    return (
        "`data/reconcile_episodes.json` 남은 글감이 "
        f"{report['remaining_days']}일입니다 "
        f"(남은 편 {report['remaining_items']}개, 기준 {LOW_QUEUE_DAYS}일 미만).\n\n"
        f"- 슬롯: {report['slot']}\n"
        f"- 현재 항목: {report.get('current_id')}\n"
        f"- 전체: {report['total']}편\n\n"
        "큐에 회차를 추가해 주세요. 예약 실행은 LLM으로 글을 만들지 않습니다.\n"
    )


def check_queue(now=None):
    report = queue_report(now=now)
    days = report["remaining_days"]
    print(
        f"글감 잔량: {report['remaining_items']}편, {days}일 "
        f"(슬롯 {report['slot']} / {report['total']}편, 기준 {LOW_QUEUE_DAYS}일)"
    )
    if days < LOW_QUEUE_DAYS:
        ok = upsert_issue(LOW_QUEUE_ISSUE, _body(report))
        return 0 if ok else 1
    close_issue(
        LOW_QUEUE_ISSUE,
        f"남은 글감이 {days}일로 {LOW_QUEUE_DAYS}일 이상입니다. 이슈를 닫습니다.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(check_queue())
