"""LLM-as-Judge: 생성된 보고서를 실제 보고서와 4가지 기준으로 비교 채점."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict

from langchain_openai import ChatOpenAI

JUDGE_SYSTEM_PROMPT = """당신은 자동 생성된 보고서의 품질을 평가하는 전문 평가자입니다.
실제 작성된 보고서를 기준으로 자동 생성 보고서를 4가지 항목에서 1~5점으로 채점하세요.

채점 기준:
1. content_similarity (내용 일치도): 실제 보고서와 같은 활동/사건을 다루는가?
   - 5: 핵심 활동이 모두 일치
   - 3: 절반 정도 일치
   - 1: 거의 다른 내용

2. format_compliance (형식 준수도): 표 구조, <br> 구분, 번호 목록(1. 2. 3.) 형식을 따르는가?
   - 5: 모든 형식 완벽 준수
   - 3: 일부 형식 누락
   - 1: 형식 미준수

3. style_similarity (문체 유사도): 간결한 보고서체 문장 스타일이 실제 보고서와 비슷한가?
   - 5: 문체 거의 동일
   - 3: 보고서체이나 어조 차이 있음
   - 1: 구어체 또는 완전히 다른 스타일

4. completeness (정보 완성도): "확인 필요" 없이 모든 섹션(주요활동, 세부내용, 배운점)이 채워졌는가?
   - 5: 모든 섹션 완성, "확인 필요" 없음
   - 3: 일부 섹션 미완성 또는 "확인 필요" 존재
   - 1: 대부분 미완성

반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{
  "content_similarity": <1-5>,
  "format_compliance": <1-5>,
  "style_similarity": <1-5>,
  "completeness": <1-5>,
  "overall": <네 점수의 평균, 소수점 둘째 자리>,
  "reasoning": "<채점 이유를 2~3문장으로>"
}"""

JUDGE_USER_PROMPT = """[실제 보고서 (기준)]
{reference}

---

[자동 생성된 보고서 (평가 대상)]
{generated}

위 두 보고서를 비교하여 채점하세요."""


@dataclass
class JudgeScore:
    content_similarity: float
    format_compliance: float
    style_similarity: float
    completeness: float
    overall: float
    reasoning: str

    def to_dict(self) -> dict:
        return asdict(self)


def judge(
    generated: str,
    reference: str,
    model: str,
    api_key: str,
    base_url: str,
) -> JudgeScore:
    """
    생성된 보고서와 reference 보고서를 LLM으로 채점한다.
    """
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=0,
    )

    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": JUDGE_USER_PROMPT.format(
                reference=reference[:6000],   # 토큰 절약: 앞부분 위주
                generated=generated[:6000],
            ),
        },
    ]

    response = llm.invoke(messages)
    raw = response.content.strip()

    # JSON 블록 추출 (```json ... ``` 감싸진 경우 대비)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"Judge 응답에서 JSON을 찾을 수 없습니다:\n{raw}")

    data = json.loads(match.group())
    scores = [
        data.get("content_similarity", 0),
        data.get("format_compliance", 0),
        data.get("style_similarity", 0),
        data.get("completeness", 0),
    ]
    overall = round(sum(scores) / len(scores), 2)

    return JudgeScore(
        content_similarity=data.get("content_similarity", 0),
        format_compliance=data.get("format_compliance", 0),
        style_similarity=data.get("style_similarity", 0),
        completeness=data.get("completeness", 0),
        overall=data.get("overall", overall),
        reasoning=data.get("reasoning", ""),
    )
