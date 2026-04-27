"""
LangChain RAG 보고서 생성기 (Langfuse 트레이싱 포함)
"""

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List
from langchain_openai import ChatOpenAI
from langchain_core.documents import Document
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langfuse import Langfuse, get_client
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler
from langfuse.types import TraceContext

from .config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL,
    LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST,
)
from .vectordb import load_vectorstore
from .department_loader import load_department_template, build_full_department_context

# Langfuse 클라이언트 초기화 (secret_key/public_key/host를 명시적으로 설정)
_langfuse = Langfuse(
    secret_key=LANGFUSE_SECRET_KEY,
    public_key=LANGFUSE_PUBLIC_KEY,
    host=LANGFUSE_HOST,
)

REPORT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """당신은 팀의 활동 내용을 분석하고 구조화된 보고서를 작성하는 전문 어시스턴트입니다.

제공된 컨텍스트를 바탕으로 다음 형식에 맞게 보고서를 작성하세요:

1. **요약** - 핵심 내용을 2~3문장으로 요약
2. **주요 활동** - 기간 내 주요 업무/이벤트 목록
3. **성과 및 완료 사항** - 완료된 작업, 달성한 목표
4. **진행 중인 사항** - 현재 진행 중인 작업
5. **이슈 및 리스크** - 발견된 문제점이나 주의 사항 (없으면 생략)
6. **다음 단계** - 향후 계획이나 액션 아이템

컨텍스트에 없는 내용은 추측하지 마세요. 정보가 부족하면 해당 항목에 '관련 정보 없음'으로 표기하세요."""),
    ("human", """{date_range_info}주제: {topic}

[참고 문서]
{context}

위 내용을 바탕으로 보고서를 작성해주세요."""),
])


def _parse_notion_dt(dt_str: str) -> Optional[datetime]:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _filter_by_date(docs: List[Document], since: Optional[datetime], until: Optional[datetime]) -> List[Document]:
    if not since and not until:
        return docs
    filtered = []
    for doc in docs:
        edited = _parse_notion_dt(doc.metadata.get("last_edited_time", ""))
        if edited is None:
            filtered.append(doc)
            continue
        if since and edited < since:
            continue
        if until and edited > until:
            continue
        filtered.append(doc)
    return filtered


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


def get_langfuse_handler(session_id: Optional[str] = None, user_id: Optional[str] = None) -> LangfuseCallbackHandler:
    trace_ctx: TraceContext = {"trace_id": uuid.uuid4().hex}
    if user_id:
        trace_ctx["user_id"] = user_id  # type: ignore[typeddict-unknown-key]
    if session_id:
        trace_ctx["session_id"] = session_id  # type: ignore[typeddict-unknown-key]
    return LangfuseCallbackHandler(trace_context=trace_ctx)


def _make_llm():
    return ChatOpenAI(model=LLM_MODEL, api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def generate_report(
    topic: str,
    k: int = 10,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """
    Args:
        topic:      보고서 주제
        k:          검색할 문서 수 (날짜 필터 후 줄어들 수 있으므로 넉넉하게)
        since:      이 시각 이후 수정된 문서만 포함 (timezone-aware datetime)
        until:      이 시각 이전 수정된 문서만 포함
        session_id: Langfuse 세션 ID
        user_id:    Langfuse 사용자 ID
    """
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id)

    print(f"🔍 '{topic}' 관련 문서 검색 중 (k={k})...")
    raw_docs = vs.similarity_search(topic, k=k)
    docs = _filter_by_date(raw_docs, since, until)
    print(f"   필터 결과: {len(raw_docs)}개 → {len(docs)}개 문서 사용")

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
):
    """스트리밍 버전 - 토큰 단위로 yield"""
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id)

    print(f"🔍 '{topic}' 관련 문서 검색 중 (k={k})...")
    raw_docs = vs.similarity_search(topic, k=k)
    docs = _filter_by_date(raw_docs, since, until)
    print(f"   필터 결과: {len(raw_docs)}개 → {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(since, until)
    context = _format_docs(docs)

    chain = REPORT_PROMPT | _make_llm() | StrOutputParser()
    for chunk in chain.stream(
        {"topic": topic, "context": context, "date_range_info": date_range_info},
        config={"callbacks": [handler]},
    ):
        yield chunk
    _langfuse.flush()


def _build_date_range_info(since: Optional[datetime], until: Optional[datetime]) -> str:
    if since or until:
        since_str = since.strftime("%Y-%m-%d") if since else "처음"
        until_str = until.strftime("%Y-%m-%d") if until else "현재"
        return f"기간: {since_str} ~ {until_str}\n"
    return ""


# ── 학과 맞춤 보고서 재생성 ────────────────────────────────────────────────

PROMPTS_DIR = Path(__file__).parent.parent / "prompts" / "department_report"


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def regenerate_for_department(
    original_report: str,
    department_id: str,
    report_date: Optional[str] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    """기존 보고서를 학과 맞춤으로 재생성합니다.

    RAG 재검색 없이 LLM만으로 원본 보고서 내용을 학과 특성에 맞게 재구성합니다.

    Args:
        original_report: 원본 보고서 (마크다운 텍스트)
        department_id:   학과 ID (config/department_templates/ 하위 YAML 파일명)
        report_date:     보고서 날짜 문자열 (미지정 시 오늘 날짜 사용)
        session_id:      Langfuse 세션 ID
        user_id:         Langfuse 사용자 ID

    Returns:
        재생성된 보고서 (마크다운 텍스트)

    Raises:
        FileNotFoundError: 해당 department_id의 템플릿이 없을 때
    """
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

    handler = get_langfuse_handler(session_id=session_id, user_id=user_id)
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

    handler = get_langfuse_handler(session_id=session_id, user_id=user_id)
    chain = prompt | _make_llm() | StrOutputParser()

    print(f"✏️  '{department_name}' 맞춤 보고서 재생성 중 (스트리밍)...")
    for chunk in chain.stream(
        {"user_prompt": user_prompt},
        config={"callbacks": [handler]},
    ):
        yield chunk
    _langfuse.flush()


# ── 편의 함수 ──────────────────────────────────────────────────────────────

def last_n_days(n: int) -> datetime:
    """n일 전 UTC datetime 반환"""
    return datetime.now(timezone.utc) - timedelta(days=n)


def this_week() -> datetime:
    """이번 주 월요일 UTC datetime 반환"""
    today = datetime.now(timezone.utc)
    return today - timedelta(days=today.weekday())
