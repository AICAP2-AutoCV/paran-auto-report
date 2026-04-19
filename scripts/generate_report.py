#!/usr/bin/env python3
"""CLI: LangChain RAG 보고서 생성 (Langfuse 트레이싱 포함)"""

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.report import generate_report, generate_report_stream, last_n_days, this_week


def parse_datetime(s: str) -> datetime:
    """YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SS → timezone-aware datetime"""
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise argparse.ArgumentTypeError(f"날짜 형식 오류: {s} (YYYY-MM-DD 사용)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LangChain RAG 보고서 생성기")
    parser.add_argument("topic", help='보고서 주제 (예: "지난 주 개발팀 활동 요약")')
    parser.add_argument("--k", type=int, default=10, help="검색할 문서 수 (기본: 10)")
    parser.add_argument("--output", "-o", help="결과 저장 경로 (.md)")
    parser.add_argument("--stream", action="store_true", help="스트리밍 출력")
    parser.add_argument("--session-id", help="Langfuse 세션 ID")
    parser.add_argument("--user-id", help="Langfuse 사용자 ID")

    # 날짜 필터 옵션
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument("--since", type=parse_datetime, metavar="YYYY-MM-DD", help="이 날짜 이후 수정된 문서만 포함")
    date_group.add_argument("--last-days", type=int, metavar="N", help="최근 N일 이내 수정된 문서만 포함")
    date_group.add_argument("--this-week", action="store_true", help="이번 주 수정된 문서만 포함")

    parser.add_argument("--until", type=parse_datetime, metavar="YYYY-MM-DD", help="이 날짜 이전 수정된 문서만 포함")

    args = parser.parse_args()

    # since 계산
    since = None
    if args.since:
        since = args.since
    elif args.last_days:
        since = last_n_days(args.last_days)
    elif args.this_week:
        since = this_week()

    session_id = args.session_id or f"report-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    print(f"\n{'=' * 60}")
    print(f"📋 보고서 생성: {args.topic}")
    if since:
        print(f"   기간: {since.strftime('%Y-%m-%d')} ~ {args.until.strftime('%Y-%m-%d') if args.until else '현재'}")
    print(f"   세션 ID: {session_id}")
    print(f"{'=' * 60}\n")

    if args.stream:
        report = ""
        for chunk in generate_report_stream(
            args.topic, k=args.k, since=since, until=args.until,
            session_id=session_id, user_id=args.user_id,
        ):
            print(chunk, end="", flush=True)
            report += chunk
        print()
    else:
        report = generate_report(
            args.topic, k=args.k, since=since, until=args.until,
            session_id=session_id, user_id=args.user_id,
        )
        print("\n" + report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"\n💾 저장 완료: {output_path}")
