"""보고서 내 전문 용어 자동 추출·강조·해설 기능"""

import json
import re
import uuid
from typing import Optional

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from ..config import OPENAI_API_KEY, OPENAI_BASE_URL, LLM_MODEL
from .generator import get_langfuse_handler, _langfuse

GLOSSARY_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """당신은 보고서에서 전문 용어를 추출하는 전문가입니다.

주어진 보고서에서 일반 독자(대학생 수준)가 이해하기 어려울 수 있는 전문 용어·기술 용어·약어를 최대 10개 추출하고,
각 용어에 대해 2-3문장의 간결한 한국어 설명을 작성하세요.

반드시 아래 JSON 형식으로만 응답하세요. 다른 텍스트는 포함하지 마세요:
{{"terms": [{{"term": "용어", "explanation": "설명"}}, ...]}}

규칙:
- 실제로 보고서에 등장하는 용어만 포함하세요
- 일반적인 한국어 단어나 이미 익숙한 단어는 제외하세요
- 기술 용어, 영어 약어, 전문 개념을 중심으로 추출하세요
- 설명은 누구나 알아들을 수 있도록 쉽게 작성하세요
- 어려운 용어가 없다면 빈 배열을 반환하세요: {{"terms": []}}"""),
    ("human", "다음 보고서에서 전문 용어를 추출하세요:\n\n{report}"),
])


def _make_llm():
    return ChatOpenAI(
        model=LLM_MODEL,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0,
    )


def extract_terms(
    markdown: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> list[dict]:
    """보고서 마크다운에서 전문 용어와 설명을 LLM으로 추출."""
    handler = get_langfuse_handler(
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id or uuid.uuid4().hex,
    )
    chain = GLOSSARY_PROMPT | _make_llm() | StrOutputParser()
    raw = chain.invoke(
        {"report": markdown},
        config={"callbacks": [handler]},
    )
    _langfuse.flush()

    # JSON 블록 추출 (```json ... ``` 래핑 허용)
    raw = raw.strip()
    json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if json_match:
        raw = json_match.group(1)

    try:
        parsed = json.loads(raw)
        terms = parsed.get("terms", [])
        return [t for t in terms if isinstance(t, dict) and "term" in t and "explanation" in t]
    except (json.JSONDecodeError, AttributeError):
        return []


def highlight_terms_in_markdown(markdown: str, terms: list[str]) -> str:
    """보고서 본문에서 전문 용어의 첫 등장을 빨간색 HTML span으로 강조.

    코드 블록(```) 및 헤딩(#으로 시작하는 줄) 내부는 건너뜀.
    """
    if not terms:
        return markdown

    # 블록을 분리: 코드 블록은 치환 대상에서 제외
    # 코드 블록을 플레이스홀더로 대체
    code_blocks: list[str] = []
    placeholder_pattern = "\x00CODE{}\x00"

    def _save_code_block(m: re.Match) -> str:
        idx = len(code_blocks)
        code_blocks.append(m.group(0))
        return placeholder_pattern.format(idx)

    result = re.sub(r"```[\s\S]*?```", _save_code_block, markdown)

    # 이미 강조된 span 내부도 건너뜀 (중복 강조 방지)
    highlighted: set[str] = set()

    for term in terms:
        if term in highlighted:
            continue
        escaped = re.escape(term)
        # 줄 단위로 헤딩·테이블 행은 건너뜀, 그 외에서 첫 1회만 치환
        lines = result.split("\n")
        replaced = False
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#"):
                continue
            if line.lstrip().startswith("|"):
                continue
            if replaced:
                break
            new_line, count = re.subn(
                rf"(?<![>\w]){escaped}(?![\w<])",
                f'<span style="color: #e74c3c; font-weight: bold;">{term}</span>',
                line,
                count=1,
            )
            if count:
                lines[i] = new_line
                replaced = True
        result = "\n".join(lines)
        if replaced:
            highlighted.add(term)

    # 코드 블록 복원
    for idx, block in enumerate(code_blocks):
        result = result.replace(placeholder_pattern.format(idx), block)

    return result


def build_glossary_section(term_explanations: list[dict]) -> str:
    """용어 해설 마크다운 섹션 생성."""
    if not term_explanations:
        return ""

    rows = "\n".join(
        f"| **{t['term']}** | {t['explanation']} |"
        for t in term_explanations
    )
    return f"\n\n---\n\n## 📚 용어 해설\n\n| 용어 | 설명 |\n|---|---|\n{rows}\n"


def add_glossary_to_report(
    markdown: str,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """보고서 마크다운에 용어 강조 + 해설 섹션을 추가하여 반환.

    Returns:
        (enriched_markdown, term_explanations)
    """
    term_explanations = extract_terms(
        markdown,
        session_id=session_id,
        user_id=user_id,
        trace_id=trace_id,
    )
    if not term_explanations:
        return markdown, []

    term_names = [t["term"] for t in term_explanations]
    enriched = highlight_terms_in_markdown(markdown, term_names)
    enriched += build_glossary_section(term_explanations)
    return enriched, term_explanations
