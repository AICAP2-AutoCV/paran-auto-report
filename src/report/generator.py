"""LangChain RAG 보고서 생성기 (Langfuse 트레이싱 포함)"""

import json
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
from qdrant_client.models import DatetimeRange, Filter, FieldCondition, MatchValue

from ..config import (
    OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL,
    LANGFUSE_SECRET_KEY, LANGFUSE_PUBLIC_KEY, LANGFUSE_HOST,
    DATA_DIR, PLAN_PAGE_ID,
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

[사용자 고정 정보 - 절대 변경 금지]
아래 값은 사용자가 직접 입력한 확정 정보입니다. 컨텍스트와 무관하게 반드시 그대로 사용하고 절대 "확인 필요"로 바꾸지 마세요.
- 팀명: {team_name}
- 학과: {department}
- 학번: {student_id}
- 성명: {name}

반드시 다음 구조를 따르세요:

[TITLE]주차별 활동 보고서[/TITLE]

## 기본 정보
| 항목 | 내용 |
|---|---|
| 제출일자 | 확인 필요 |
| 팀명 | {team_name} |
| 학과 | {department} |
| 학번 | {student_id} |
| 성명 | {name} |
| 도전과제명 | 사용자가 요청한 주제 |
| 보고 기간 | 확인된 경우만 작성 |

## 주차 활동내용

## 1. 주요활동

### 가. 최초 계획
- [계획서 - 도전과제 추진일정]에 주차별 계획 표가 있으면, 보고 기간에 해당하는 주차의 팀/개인 계획을 그대로 사용하세요.
- 해당 정보가 없으면 "확인 필요"로 작성하세요.
- 계획 내용은 반드시 번호 목록 형식으로 작성하세요. 각 항목은 `<br>`로 구분합니다. 예: `1. 첫 번째 계획<br>2. 두 번째 계획`
- 계획 항목을 쉼표로 나열하지 말고, 반드시 번호를 붙여 한 줄씩 작성하세요.

| 구분 | 계획 내용 |
|---|---|
| 팀 | 1. 첫 번째 팀 계획<br>2. 두 번째 팀 계획 |
| 개인 | 1. 첫 번째 개인 계획<br>2. 두 번째 개인 계획 |

### 나. 실제 활동내용 및 목표달성 여부
- 팀의 투입시간은 반드시 '-'로 표시하고, 개인의 투입시간만 컨텍스트에서 찾아 숫자로 작성하세요.
- 투입시간(시간 수)과 목표달성 여부(달성/부분 달성/미달성)가 컨텍스트에 있으면 반드시 개인 행에 작성하세요.
- 사용자 역할이 제공된 경우, 컨텍스트에서 해당 역할과 관련된 활동을 찾아 개인 행에 구체적으로 작성하세요.
- 역할 관련 활동이 컨텍스트에 있으면 반드시 "확인 필요" 대신 실제 내용을 채우세요.
- 실제 활동내용은 반드시 번호 목록 형식으로 작성하세요. 각 항목은 `<br>`로 구분합니다. 예: `1. 첫 번째 활동 내용<br>2. 두 번째 활동 내용<br>3. 세 번째 활동 내용`
- 활동 항목은 쉼표로 나열하지 말고, 반드시 번호를 붙여 한 줄씩 작성하세요.

| 구분 | 투입시간 | 실제 활동내용 | 목표달성 여부 |
|---|---:|---|---|
| 팀 | - | 1. 첫 번째 팀 활동<br>2. 두 번째 팀 활동<br>3. 세 번째 팀 활동 | 달성/부분 달성/미달성 |
| 개인 | 확인된 경우만 작성 (예: 5시간) | 1. 첫 번째 개인 활동<br>2. 두 번째 개인 활동 | 달성/부분 달성/미달성 |

## 2. 세부내용
원문 근거가 있는 활동을 중심으로 소제목을 나누어 구체적으로 작성합니다. 단순 요약이 아니라 무엇을 했고, 왜 했고, 어떤 의미가 있는지 보고서 문장으로 설명합니다.

## 3. 배운점
활동을 통해 얻은 인사이트, 협업상 배운 점, 다음 활동에 반영할 점을 작성합니다.

작성 규칙:
- 컨텍스트에 없는 사실, 수치, 성과, 실험 결과, 기관명은 만들지 마세요.
- 정보가 부족한 칸은 억지로 채우지 말고 "확인 필요"라고 쓰세요.
- 표는 위 형식을 유지하되, 실제 활동내용 칸은 반드시 번호 목록(`<br>` 구분)으로 작성하세요.
- 제목 태그 [TITLE]...[/TITLE]는 반드시 첫 줄에 한 번만 작성하세요."""),
    ("human", """{role_info}{date_range_info}주제: {topic}

[계획서 - 도전과제 추진일정]
{plan_context}

[활동 기록 참고 문서]
{context}

위 내용을 바탕으로 보고서를 작성해주세요.
"가. 최초 계획" 표는 반드시 [계획서 - 도전과제 추진일정]에서 보고 기간에 해당하는 주차 내용을 찾아 작성하세요.
{role_instruction}"""),
])

PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts" / "department_report"


def _build_qdrant_filter(since: Optional[datetime], until: Optional[datetime]) -> Optional[Filter]:
    """since/until → Qdrant 메타데이터 필터 (없으면 None)"""
    if not since and not until:
        return None
    conditions = []
    conditions.append(FieldCondition(
        key="metadata.prop_날짜",
        range=DatetimeRange(gte=since, lte=until),
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


def _collect_images_from_docs(docs: List[Document], max_images: int = 4) -> List[dict]:
    """검색된 문서 메타데이터에서 관련 이미지 후보를 중복 제거해 추출."""
    images = []
    seen = set()
    for doc in docs:
        image_paths = doc.metadata.get("image_paths") or []
        image_descriptions = doc.metadata.get("image_descriptions") or []
        if isinstance(image_paths, str):
            image_paths = [image_paths]
        if isinstance(image_descriptions, str):
            image_descriptions = [image_descriptions]

        for idx, image_path in enumerate(image_paths):
            if not image_path or image_path in seen:
                continue
            seen.add(image_path)
            description = image_descriptions[idx] if idx < len(image_descriptions) else ""
            images.append({
                "path": image_path,
                "description": description or doc.metadata.get("section_title", "") or doc.metadata.get("page_title", ""),
                "source": doc.metadata.get("page_title", ""),
                "section_title": doc.metadata.get("section_title", ""),
            })
            if len(images) >= max_images:
                return images

        raw = doc.metadata.get("images_json")
        if not raw:
            continue
        try:
            candidates = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(candidates, list):
            continue

        for image in candidates:
            if not isinstance(image, dict):
                continue
            image_ref = image.get("path")
            if not image_ref and image.get("block_id"):
                page_id = doc.metadata.get("page_id", "")
                url_path = (image.get("url") or "").split("?", 1)[0]
                suffix = Path(url_path).suffix or ".png"
                candidate = f"notion_images/{page_id}_{image['block_id']}{suffix}"
                if (Path(DATA_DIR) / candidate).exists():
                    image_ref = candidate
            image_ref = image_ref or image.get("url")

            key = image_ref or image.get("block_id")
            if not key or key in seen:
                continue
            seen.add(key)
            enriched = {
                **image,
                "path": image_ref,
                "page_title": doc.metadata.get("page_title", ""),
                "section_title": image.get("section_title") or doc.metadata.get("section_title", ""),
            }
            if not enriched.get("description"):
                parts = [enriched.get("caption"), enriched.get("page_title"), enriched.get("section_title")]
                enriched["description"] = " - ".join(p for p in parts if p)
            images.append(enriched)
            if len(images) >= max_images:
                return images
    return images


def _fetch_plan_context(vs) -> str:
    """계획서 페이지의 도전과제 추진일정 내용을 Qdrant에서 추출."""
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


def _build_date_range_info(since: Optional[datetime], until: Optional[datetime]) -> str:
    if since or until:
        since_str = since.strftime("%Y-%m-%d") if since else "처음"
        until_str = until.strftime("%Y-%m-%d") if until else "현재"
        return f"기간: {since_str} ~ {until_str}\n"
    return ""


def _build_role_info(role: Optional[str]) -> str:
    if role:
        return f"사용자 역할: {role}\n"
    return ""


def _build_user_field(value: Optional[str], fallback: str = "확인 필요") -> str:
    return value.strip() if value and value.strip() else fallback


def _build_role_instruction(role: Optional[str]) -> str:
    if role:
        return (
            f'\n"나. 실제 활동내용 및 목표달성 여부" 표의 개인 행은 반드시 채워야 합니다. '
            f'사용자 역할은 "{role}"입니다. '
            f"컨텍스트에서 이 역할과 직접 관련된 활동을 찾아 작성하세요. "
            f"직접적인 언급이 없더라도 팀 활동 내용을 바탕으로 {role} 역할을 맡은 사람이 "
            f"수행했을 구체적인 활동을 추론해 작성하세요. "
            f'개인 행의 실제 활동내용에 "확인 필요"는 절대 사용하지 마세요.'
        )
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
    role: Optional[str] = None,
) -> str:
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)

    plan_context = _fetch_plan_context(vs)

    qdrant_filter = _build_qdrant_filter(since, until)
    print(f"🔍 '{topic}' 관련 문서 검색 중 (k={k})...")
    docs = vs.similarity_search(topic, k=k, filter=qdrant_filter)
    print(f"   검색 결과: {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(since, until)
    context = _format_docs(docs)

    chain = REPORT_PROMPT | _make_llm() | StrOutputParser()
    result = chain.invoke(
        {
            "topic": topic,
            "context": context,
            "date_range_info": date_range_info,
            "plan_context": plan_context,
            "role_info": _build_role_info(role),
            "role_instruction": _build_role_instruction(role),
        },
        config={"callbacks": [handler]},
    )
    _langfuse.flush()
    print("✅ 보고서 생성 완료")
    return result


def generate_report_with_images(
    topic: str,
    k: int = 10,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    max_images: int = 4,
    role: Optional[str] = None,
    team_name: Optional[str] = None,
    student_id: Optional[str] = None,
    department: Optional[str] = None,
    name: Optional[str] = None,
) -> dict:
    """보고서 본문과 검색 문맥에서 나온 관련 이미지를 함께 반환."""
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)

    plan_context = _fetch_plan_context(vs)

    qdrant_filter = _build_qdrant_filter(since, until)
    print(f"🔍 '{topic}' 관련 문서/이미지 검색 중 (k={k})...")
    docs = vs.similarity_search(topic, k=k, filter=qdrant_filter)
    print(f"   검색 결과: {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(since, until)
    context = _format_docs(docs)
    images = _collect_images_from_docs(docs, max_images=max_images)

    chain = REPORT_PROMPT | _make_llm() | StrOutputParser()
    report = chain.invoke(
        {
            "topic": topic,
            "context": context,
            "date_range_info": date_range_info,
            "plan_context": plan_context,
            "role_info": _build_role_info(role),
            "role_instruction": _build_role_instruction(role),
            "team_name": _build_user_field(team_name),
            "student_id": _build_user_field(student_id),
            "department": _build_user_field(department),
            "name": _build_user_field(name),
        },
        config={"callbacks": [handler]},
    )
    _langfuse.flush()
    print(f"✅ 보고서 생성 완료 (관련 이미지 {len(images)}개)")
    return {"report": report, "images": images, "source_count": len(docs)}


def generate_report_stream(
    topic: str,
    k: int = 10,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    role: Optional[str] = None,
    team_name: Optional[str] = None,
    student_id: Optional[str] = None,
    department: Optional[str] = None,
    name: Optional[str] = None,
):
    """스트리밍 버전 - 토큰 단위로 yield"""
    vs = load_vectorstore()
    handler = get_langfuse_handler(session_id=session_id, user_id=user_id, trace_id=trace_id)

    plan_context = _fetch_plan_context(vs)

    qdrant_filter = _build_qdrant_filter(since, until)
    print(f"🔍 '{topic}' 관련 문서 검색 중 (k={k})...")
    docs = vs.similarity_search(topic, k=k, filter=qdrant_filter)
    print(f"   검색 결과: {len(docs)}개 문서 사용")

    date_range_info = _build_date_range_info(since, until)
    context = _format_docs(docs)

    chain = REPORT_PROMPT | _make_llm() | StrOutputParser()
    for chunk in chain.stream(
        {
            "topic": topic,
            "context": context,
            "date_range_info": date_range_info,
            "plan_context": plan_context,
            "role_info": _build_role_info(role),
            "role_instruction": _build_role_instruction(role),
            "team_name": _build_user_field(team_name),
            "student_id": _build_user_field(student_id),
            "department": _build_user_field(department),
            "name": _build_user_field(name),
        },
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
