"""이미지 설명 생성 헬퍼."""

import base64
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from openai import OpenAI

from ..config import OPENAI_API_KEY, OPENAI_BASE_URL, VISION_MODEL, VISION_MAX_TOKENS, DATA_DIR


class OpenAIVisionDescriber:
    def __init__(self):
        self.client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        self.model = VISION_MODEL
        self.max_tokens = VISION_MAX_TOKENS

    def _load_image_b64(self, image_path: str) -> tuple[str, str]:
        if image_path.startswith(("http://", "https://")):
            response = requests.get(image_path, timeout=30)
            response.raise_for_status()
            data = response.content
        else:
            path = Path(image_path)
            if not path.is_absolute():
                path = Path(DATA_DIR) / image_path
            data = path.read_bytes()

        media_type = "image/png" if image_path.lower().split("?")[0].endswith(".png") else "image/jpeg"
        return base64.b64encode(data).decode("utf-8"), media_type

    def describe_image(self, image_path: str, context: Dict[str, Any]) -> str:
        try:
            image_b64, media_type = self._load_image_b64(image_path)
        except Exception as e:
            return f"이미지 로드 실패: {e}"

        prompt = f"""다음 이미지를 주차별 활동 보고서에 활용할 수 있게 한국어로 설명하세요.

페이지 제목: {context.get('page_title', '')}
섹션 제목: {context.get('section_title', '')}
이미지 앞 문맥: {context.get('text_before', '')[:300]}
이미지 뒤 문맥: {context.get('text_after', '')[:300]}

요구사항:
- 이미지에서 보이는 핵심 대상, 그래프/표/결과, 의미를 2~4문장으로 설명하세요.
- 문맥에 없는 성과나 수치는 만들지 마세요.
- 보고서 하단 캡션으로도 쓸 수 있게 간결하게 작성하세요."""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{image_b64}"},
                        },
                    ],
                }],
                max_tokens=self.max_tokens,
                temperature=0,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            return f"이미지 설명 생성 실패: {e}"


def get_vision_describer() -> Optional[OpenAIVisionDescriber]:
    if not OPENAI_API_KEY:
        print("⚠️ Vision 모델 API 키 없음 - 이미지 설명 생성 비활성화")
        return None
    return OpenAIVisionDescriber()
