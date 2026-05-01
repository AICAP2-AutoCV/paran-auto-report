"""Langfuse feedback helpers."""

from typing import Optional

from langfuse import Langfuse

from .config import LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY


_langfuse_client: Optional[Langfuse] = None


def get_langfuse_client() -> Optional[Langfuse]:
    """Return a singleton Langfuse client when credentials are configured."""
    global _langfuse_client

    if _langfuse_client is None:
        if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
            return None
        _langfuse_client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )

    return _langfuse_client


def save_feedback(
    trace_id: str,
    score: int,
    comment: Optional[str] = None,
    feedback_type: str = "user_satisfaction",
) -> bool:
    """Store user feedback as a normalized Langfuse score."""
    client = get_langfuse_client()
    if client is None:
        raise ValueError("Langfuse 키가 설정되지 않았습니다. LANGFUSE_PUBLIC_KEY와 LANGFUSE_SECRET_KEY를 확인해 주세요.")

    normalized_score = score / 10.0
    try:
        client.create_score(
            trace_id=trace_id,
            name=feedback_type,
            value=normalized_score,
            comment=comment,
        )
        client.flush()
        return True
    except Exception as exc:
        print(f"피드백 저장 실패: {exc}")
        return False
