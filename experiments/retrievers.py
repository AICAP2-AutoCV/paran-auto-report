"""Retriever 팩토리: dense / bm25 / rrf / rrf_multiquery"""

from __future__ import annotations

from typing import Any, List, Optional

from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_qdrant import QdrantVectorStore
from langchain_community.retrievers import BM25Retriever
from langchain_classic.retrievers import EnsembleRetriever, MultiQueryRetriever
from langchain_openai import ChatOpenAI
from qdrant_client.models import Filter


class _EmptyRetriever(BaseRetriever):
    """문서가 0개일 때 항상 빈 결과를 반환하는 fallback retriever."""

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        return []


def _load_all_docs(vs: QdrantVectorStore, qdrant_filter: Optional[Filter] = None) -> List[Document]:
    """Qdrant 컬렉션의 전체 문서를 스크롤로 가져온다 (BM25용)."""
    client = vs.client
    collection_name = vs.collection_name

    all_docs: List[Document] = []
    offset = None

    while True:
        result, next_offset = client.scroll(
            collection_name=collection_name,
            scroll_filter=qdrant_filter,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in result:
            payload = point.payload or {}
            metadata = payload.get("metadata", payload)
            content = payload.get("page_content", "")
            if content:
                all_docs.append(Document(page_content=content, metadata=metadata))
        if next_offset is None:
            break
        offset = next_offset

    return all_docs


def get_retriever(
    vs: QdrantVectorStore,
    retriever_type: str,
    k: int = 10,
    qdrant_filter: Optional[Filter] = None,
    llm_model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> BaseRetriever:
    """
    retriever_type:
      - "dense"         : 벡터 유사도 검색
      - "bm25"          : BM25 키워드 검색
      - "rrf"           : BM25 + Dense 앙상블 (Reciprocal Rank Fusion)
      - "rrf_multiquery": RRF + LLM 쿼리 확장
    """
    search_kwargs = {"k": k}
    if qdrant_filter:
        search_kwargs["filter"] = qdrant_filter

    if retriever_type == "dense":
        return vs.as_retriever(search_kwargs=search_kwargs)

    if retriever_type == "bm25":
        docs = _load_all_docs(vs, qdrant_filter)
        if not docs:
            return _EmptyRetriever()
        return BM25Retriever.from_documents(docs, k=k)

    if retriever_type in ("rrf", "rrf_multiquery"):
        # BM25: 날짜 필터는 Python 레벨에서 적용 (이미 _load_all_docs에서 처리)
        docs = _load_all_docs(vs, qdrant_filter)
        if not docs:
            return _EmptyRetriever()
        bm25 = BM25Retriever.from_documents(docs, k=k * 2)

        # Dense: 2배 후보 확보 후 RRF에서 rerank
        dense_kwargs = {"k": k * 2}
        if qdrant_filter:
            dense_kwargs["filter"] = qdrant_filter
        dense = vs.as_retriever(search_kwargs=dense_kwargs)

        rrf = EnsembleRetriever(
            retrievers=[bm25, dense],
            weights=[0.5, 0.5],
            c=60,  # RRF 상수
            id_key="chunk_id",
        )

        if retriever_type == "rrf":
            return rrf

        # rrf_multiquery: LLM으로 쿼리 3개 생성 후 각각 RRF 검색, 결과 중복 제거
        llm = ChatOpenAI(
            model=llm_model,
            api_key=api_key,
            base_url=base_url,
            temperature=0.7,
        )
        return MultiQueryRetriever.from_llm(retriever=rrf, llm=llm)

    raise ValueError(f"지원하지 않는 retriever_type: {retriever_type!r}")
