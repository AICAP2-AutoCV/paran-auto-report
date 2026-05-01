#!/usr/bin/env python3
"""CLI: Notion → Qdrant Vector DB 구축"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion import build_vectordb, load_vectorstore

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Notion → Qdrant Vector DB 빌더")
    sub = parser.add_subparsers(dest="command")

    build_cmd = sub.add_parser("build", help="Vector DB 구축")
    build_cmd.add_argument("--force", action="store_true", help="컬렉션 전체 재생성")
    build_cmd.add_argument("--limit", type=int, default=None, help="수집할 페이지 수 제한")

    search_cmd = sub.add_parser("search", help="Vector DB 검색 테스트")
    search_cmd.add_argument("query", help="검색 쿼리")
    search_cmd.add_argument("--k", type=int, default=5)

    args = parser.parse_args()

    if args.command == "build":
        build_vectordb(force_recreate=args.force, limit=args.limit)
    elif args.command == "search":
        vs = load_vectorstore()
        results = vs.similarity_search(args.query, k=args.k)
        for i, doc in enumerate(results, 1):
            title = doc.metadata.get("page_title", "")
            section = doc.metadata.get("section_title", "")
            print(f"\n[{i}] {title} > {section}")
            print(f"    {doc.page_content[:200]}...")
    else:
        parser.print_help()
