#!/usr/bin/env python3
"""2개 모델로 Word 보고서 생성 비교 (이미지 포함)"""

import sys
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from qdrant_client.models import DatetimeRange, Filter, FieldCondition, MatchValue

from src.config import OPENAI_API_KEY, OPENAI_BASE_URL, DATA_DIR, PLAN_PAGE_ID
from src.ingestion import load_vectorstore
from src.report.generator import (
    REPORT_PROMPT,
    _format_docs,
    _collect_images_from_docs,
    _build_date_range_info,
    _build_user_field,
    get_langfuse_handler,
)
from src.document import DocumentGenerator


MODELS = [
    {
        "name": "claude-opus-4-7",
        "model_id": "anthropic/claude-opus-4-7",
    },
    {
        "name": "gemini-3-flash",
        "model_id": "google/gemini-3-flash-preview",
    },
]

TOPIC = "1주차 활동 보고서"
SINCE = datetime(2025, 9, 1, tzinfo=timezone.utc)
UNTIL = datetime(2025, 9, 7, tzinfo=timezone.utc)
K = 10
MAX_IMAGES = 4

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output" / "comparison"


def generate_with_model(model_id: str, model_name: str, vs, plan_context: str) -> dict:
    print(f"\n{'='*60}")
    print(f"🤖 모델: {model_name} ({model_id})")
    print(f"{'='*60}")

    qdrant_filter = Filter(must=[
        FieldCondition(
            key="metadata.prop_날짜",
            range=DatetimeRange(gte=SINCE, lte=UNTIL),
        )
    ])

    print(f"🔍 '{TOPIC}' 관련 문서/이미지 검색 중 (k={K})...")
    docs = vs.similarity_search(TOPIC, k=K, filter=qdrant_filter)
    print(f"   검색 결과: {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(SINCE, UNTIL)
    context = _format_docs(docs)
    images = _collect_images_from_docs(docs, max_images=MAX_IMAGES)
    print(f"   관련 이미지: {len(images)}개 발견")

    llm = ChatOpenAI(
        model=model_id,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0,
    )

    session_id = f"comparison-{model_name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    handler = get_langfuse_handler(session_id=session_id)
    chain = REPORT_PROMPT | llm | StrOutputParser()

    print(f"✍️  보고서 생성 중...")
    start = datetime.now()
    report = chain.invoke(
        {
            "topic": TOPIC,
            "context": context,
            "date_range_info": date_range_info,
            "plan_context": plan_context,
            "role_info": "",
            "role_instruction": "",
            "team_name": _build_user_field(None),
            "student_id": _build_user_field(None),
            "department": _build_user_field(None),
            "name": _build_user_field(None),
        },
        config={"callbacks": [handler]},
    )
    elapsed = (datetime.now() - start).total_seconds()
    print(f"✅ 생성 완료 ({elapsed:.1f}초)")

    return {"report": report, "images": images, "elapsed": elapsed}


def _fetch_plan_context(vs) -> str:
    if not PLAN_PAGE_ID:
        return ""
    plan_filter = Filter(must=[
        FieldCondition(key="metadata.page_id", match=MatchValue(value=PLAN_PAGE_ID))
    ])
    docs = vs.similarity_search(
        "도전과제 추진일정 주차 팀 개인 계획",
        k=20,
        filter=plan_filter,
    )
    if not docs:
        return ""
    docs.sort(key=lambda d: d.metadata.get("chunk_id", ""))
    return "\n\n".join(d.page_content for d in docs)


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("📚 벡터 DB 로드 중...")
    vs = load_vectorstore()
    plan_context = _fetch_plan_context(vs)
    print(f"   계획서 컨텍스트: {len(plan_context)}자")

    gen = DocumentGenerator()
    results = {}

    for m in MODELS:
        payload = generate_with_model(m["model_id"], m["name"], vs, plan_context)
        results[m["name"]] = payload

        output_path = OUTPUT_DIR / f"week1_{m['name']}.docx"
        created_date = datetime.now().strftime("%Y-%m-%d")
        gen.generate_from_markdown(
            payload["report"],
            str(output_path),
            title=TOPIC,
            author="비교실험",
            created_date=created_date,
            images=payload["images"],
        )
        print(f"💾 저장: {output_path}")

    print("\n\n" + "=" * 60)
    print("📊 생성 시간 비교")
    print("=" * 60)
    for name, r in results.items():
        print(f"  {name:<20} {r['elapsed']:>6.1f}초  이미지 {len(r['images'])}개")
    print("=" * 60)
    print(f"\n출력 폴더: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
