"""LangChain RAG 보고서 생성기 (Langfuse 트레이싱 포함)"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List

from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langfuse import Langfuse
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
from langfuse.types import TraceContext
from qdrant_client.models import DatetimeRange, Filter, FieldCondition

from ..config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL,
    LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST,
)
from ..ingestion import load_vectorstore
from .department import load_department_template, build_full_department_context


_langfuse = Langfuse(
    secret_key=LANGFUSE_SECRET_KEY,
    public_key=LANGFUSE_PUBLIC_KEY,
    host=LANGFUSE_HOST,
)

REPORT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """당신은 주차별 활동 보고서를 작성하는 전문 어시스턴트입니다.

제공된 컨텍스트를 바탕으로 학교 제출용 보고서처럼 명확한 형식으로 작성하세요. 문체는 간결한 보고서체를 사용합니다.

반드시 다음 구조를 따르세요:

[TITLE]주차별 활동 보고서[/TITLE]

## 기본 정보
| 항목 | 내용 |
|---|---|
| 제출일자 | 확인 필요 |
| 팀명 | 확인 필요 |
| 학과 | 확인 필요 |
| 학번 | 확인 필요 |
| 성명 | 확인 필요 |
| 도전과제명 | 사용자가 요청한 주제 |
| 보고 기간 | 확인된 경우만 작성 |

## 주차 활동내용

## 1. 주요활동

### 가. 최초 계획
| 구분 | 계획 내용 |
|---|---|
| 팀 | 원래 계획된 팀 활동 |
| 개인 | 원래 계획된 개인 활동 |

### 나. 실제 활동내용 및 목표달성 여부
| 구분 | 투입시간 | 실제 활동내용 | 목표달성 여부 |
|---|---:|---|---|
| 팀 | 확인된 경우만 작성 | 실제 수행한 팀 활동 | 달성/부분 달성/미달성 |
| 개인 | 확인된 경우만 작성 | 실제 수행한 개인 활동 | 달성/부분 달성/미달성 |

## 2. 세부내용
원문 근거가 있는 활동을 중심으로 소제목을 나누어 구체적으로 작성합니다. 단순 요약이 아니라 무엇을 했고, 왜 했고, 어떤 의미가 있는지 보고서 문장으로 설명합니다.

## 3. 배운점
활동을 통해 얻은 인사이트, 협업상 배운 점, 다음 활동에 반영할 점을 작성합니다.

작성 규칙:
- 컨텍스트에 없는 사실, 수치, 성과, 실험 결과, 기관명은 만들지 마세요.
- 정보가 부족한 칸은 억지로 채우지 말고 "확인 필요"라고 쓰세요.
- 표는 위 형식을 유지하되, 내용이 많으면 줄바꿈 대신 문장형 요약으로 작성하세요.
- 제목 태그 [TITLE]...[/TITLE]는 반드시 첫 줄에 한 번만 작성하세요."""),
    ("human", """{date_range_info}주제: {topic}

[참고 문서]
{context}

위 내용을 바탕으로 보고서를 작성해주세요."""),
])

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts" / "department_report"


def _build_qdrant_filter(since: Optional[datetime], until: Optional[datetime]) -> Optional[Filter]:
    """since/until → Qdrant 메타데이터 필터 (없으면 None)"""
    if not since and not until:
        return None
    conditions = []
    if since:
        conditions.append(FieldCondition(
            key="last_edited_time",
            range=DatetimeRange(gte=since),
        ))
    if until:
        conditions.append(FieldCondition(
            key="last_edited_time",
            range=DatetimeRange(lte=until),
        ))
    return Filter(must=conditions)


def _format_docs(docs: List[Document]) -> str:
    if not docs:
        return "관련 문서가 없습니다."
    parts = []
    for i, doc in enumerate(docs, 1):
        title = doc.metadata.get("page_title", "")
        section = doc.metadata.get("section_title", "")
        edited = doc.metadata.get("last_edited_time", "")[:10]
        header = f"[{i}] {title}" + (f" > {section}" if section != title else "") + (f" ({edited})" if edited else "")
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


def _build_date_range_info(since: Optional[datetime], until: Optional[datetime]) -> str:
    if since or until:
        since_str = since.strftime("%Y-%m-%d") if since else "처음"
        until_str = until.strftime("%Y-%m-%d") if until else "현재"
        return f"기간: {since_str} ~ {until_str}\n"
    return ""


def get_langfuse_handler(
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> LangfuseCallbackHandler:
    trace_ctx: TraceContext = {"trace_id": trace_id or uuid.uuid4().hex}
    if user_id:
        trace_ctx["user_id"] = user_id  # type: ignore[typeddict-unknown-key]
    if session_id:
        trace_ctx["session_id"] = session_id  # type: ignore[typeddict-unknown-key]
    return LangfuseCallbackHandler(trace_context=trace_ctx)


def _make_llm():
    return ChatOpenAI(model=LLM_MODEL, api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL, temperature=0)


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def generate_report(
    topic: str,
    k: int = 10,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)

    qdrant_filter = _build_qdrant_filter(since, until)
    print(f"🔍 '{topic}' 관련 문서 검색 중 (k={k})...")
    docs = vs.similarity_search(topic, k=k, filter=qdrant_filter)
    print(f"   검색 결과: {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(since, until)
    context = _format_docs(docs)

    chain = REPORT_PROMPT | _make_llm() | StrOutputParser()
    result = chain.invoke(
        {"topic": topic, "context": context, "date_range_info": date_range_info},
        config={"callbacks": [handler]},
    )
    _langfuse.flush()
    print("✅ 보고서 생성 완료")
    return result


def generate_report_stream(
    topic: str,
    k: int = 10,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
):
    """스트리밍 버전 - 토큰 단위로 yield"""
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)

    qdrant_filter = _build_qdrant_filter(since, until)
    print(f"🔍 '{topic}' 관련 문서 검색 중 (k={k})...")
    docs = vs.similarity_search(topic, k=k, filter=qdrant_filter)
    print(f"   검색 결과: {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(since, until)
    context = _format_docs(docs)

    chain = REPORT_PROMPT | _make_llm() | StrOutputParser()
    for chunk in chain.stream(
        {"topic": topic, "context": context, "date_range_info": date_range_info},
        config={"callbacks": [handler]},
    ):
        yield chunk
    _langfuse.flush()


def regenerate_for_department(
    original_report: str,
    department_id: str,
    report_date: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> str:
    template = load_department_template(department_id)
    dept_info = template.get("department", {})
    department_name = dept_info.get("name", department_id)
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")

    department_context = build_full_department_context(department_id)
    if not department_context:
        raise ValueError(f"학과 컨텍스트 생성 실패: {department_id}")

    system_prompt = _load_prompt("system_prompt.txt")
    regen_template = _load_prompt("regeneration_prompt.txt")

    user_prompt = (
        regen_template
        .replace("{department_name}", department_name)
        .replace("{department_context}", department_context)
        .replace("{original_report}", original_report)
        .replace("{report_date}", report_date)
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{user_prompt}"),
    ])

    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)
    chain = prompt | _make_llm() | StrOutputParser()

    print(f"✏️  '{department_name}' 맞춤 보고서 재생성 중...")
    result = chain.invoke(
        {"user_prompt": user_prompt},
        config={"callbacks": [handler]},
    )
    _langfuse.flush()
    print("✅ 재생성 완료")
    return result


def regenerate_for_department_stream(
    original_report: str,
    department_id: str,
    report_date: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
):
    """학과 맞춤 보고서 재생성 스트리밍 버전 - 토큰 단위로 yield"""
    template = load_department_template(department_id)
    dept_info = template.get("department", {})
    department_name = dept_info.get("name", department_id)
    report_date = report_date or datetime.now().strftime("%Y-%m-%d")

    department_context = build_full_department_context(department_id)
    system_prompt = _load_prompt("system_prompt.txt")
    regen_template = _load_prompt("regeneration_prompt.txt")

    user_prompt = (
        regen_template
        .replace("{department_name}", department_name)
        .replace("{department_context}", department_context)
        .replace("{original_report}", original_report)
        .replace("{report_date}", report_date)
    )

    prompt = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        ("human", "{user_prompt}"),
    ])

    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)
    chain = prompt | _make_llm() | StrOutputParser()

    print(f"✏️  '{department_name}' 맞춤 보고서 재생성 중 (스트리밍)...")
    for chunk in chain.stream(
        {"user_prompt": user_prompt},
        config={"callbacks": [handler]},
    ):
        yield chunk
    _langfuse.flush()


def last_n_days(n: int) -> datetime:
    """n일 전 UTC datetime 반환"""
    return datetime.now(timezone.utc) - timedelta(days=n)


def this_week() -> datetime:
    """이번 주 월요일 UTC datetime 반환"""
    today = datetime.now(timezone.utc)
    return today - timedelta(days=today.weekday())
