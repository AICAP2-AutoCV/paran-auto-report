"""Qdrant 벡터스토어 구축 및 로드"""

import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Optional, List

from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from langchain_qdrant import QdrantVectorStore

from ..config import (
    NOTION_TOKEN, NOTION_DATABASE_ID,
    NOTION_SOURCE_PAGES, NOTION_SOURCE_DATABASES,
    OPENAI_API_KEY, OPENAI_BASE_URL, EMBEDDING_MODEL,
    QDRANT_PATH, QDRANT_COLLECTION,
)
from .models import PageChunk
from .notion import NotionCollector
from .chunker import chunk_page
from .embedder import OpenAIEmbedder
from .vision import get_vision_describer


def _normalize_datetime_payload(value: str) -> str:
    """Notion date 값을 Qdrant DatetimeRange가 읽을 수 있는 RFC3339 문자열로 변환."""
    if len(value) == 10:
        dt = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _chunk_to_document(chunk: PageChunk) -> Document:
    metadata = {
        "chunk_id": chunk.chunk_id,
        "page_id": chunk.page_id,
        "page_title": chunk.page_title,
        "section_title": chunk.section_title,
        "created_time": chunk.created_time,
        "last_edited_time": chunk.last_edited_time,
        "has_image": chunk.has_image,
        "image_paths": chunk.image_paths,
        "image_descriptions": chunk.image_descriptions,
        "images_json": json.dumps(chunk.images, ensure_ascii=False),
    }
    for k, v in chunk.properties.items():
        if isinstance(v, (str, int, float, bool)):
            metadata[f"prop_{k}"] = v
        elif isinstance(v, list):
            metadata[f"prop_{k}"] = ", ".join(str(i) for i in v)
        elif isinstance(v, dict) and "start" in v and v["start"]:
            metadata[f"prop_{k}"] = _normalize_datetime_payload(v["start"])
    return Document(page_content=chunk.combined_text or chunk.text, metadata=metadata)


def _make_embedder() -> OpenAIEmbedder:
    return OpenAIEmbedder(
        api_key=OPENAI_API_KEY,
        model=EMBEDDING_MODEL,
        base_url=OPENAI_BASE_URL,
    )


def _collect_all_pages(limit: Optional[int] = None) -> List[dict]:
    """설정된 모든 소스(단일 페이지 + 데이터베이스)에서 페이지를 수집."""
    pages = []

    # 다중 소스가 설정된 경우 우선 사용
    source_pages = NOTION_SOURCE_PAGES
    source_databases = NOTION_SOURCE_DATABASES

    # 아무 소스도 없으면 기존 단일 DB로 폴백
    if not source_pages and not source_databases:
        if not NOTION_DATABASE_ID:
            raise RuntimeError("수집할 Notion 소스가 없습니다. NOTION_SOURCE_PAGES, NOTION_SOURCE_DATABASES 또는 NOTION_DATABASE_ID를 설정하세요.")
        source_databases = [NOTION_DATABASE_ID]

    # 단일 페이지 수집 (신청서, 계획서 등)
    if source_pages:
        collector = NotionCollector(NOTION_TOKEN)
        for page_id in source_pages:
            page_data = collector.collect_single_page(page_id)
            pages.append(page_data)

    # 데이터베이스 수집 (회의록 등)
    for db_id in source_databases:
        collector = NotionCollector(NOTION_TOKEN, db_id)
        db_pages = collector.collect_all(limit=limit)
        pages.extend(db_pages)

    return pages


def build_vectordb(force_recreate: bool = False, limit: Optional[int] = None) -> QdrantVectorStore:
    print("=" * 60)
    print("🚀 Vector DB 구축 시작")
    print(f"   모델: {EMBEDDING_MODEL}")
    print(f"   컬렉션: {QDRANT_COLLECTION}")
    print(f"   경로: {QDRANT_PATH}")
    if NOTION_SOURCE_PAGES:
        print(f"   단일 페이지: {len(NOTION_SOURCE_PAGES)}개")
    if NOTION_SOURCE_DATABASES:
        print(f"   데이터베이스: {len(NOTION_SOURCE_DATABASES)}개")
    print("=" * 60)

    pages = _collect_all_pages(limit=limit)

    print("\n🖼️  Vision 모델 준비 중...")
    vision_model = get_vision_describer()

    print("\n✂️  청킹 중...")
    all_chunks: List[PageChunk] = []
    for page in pages:
        chunks = chunk_page(page, vision_model=vision_model)
        all_chunks.extend(chunks)
        image_chunks = sum(1 for chunk in chunks if chunk.has_image)
        print(f"  {page['title']}: {len(chunks)}개 청크, 이미지 청크 {image_chunks}개")
    print(f"✅ 총 {len(all_chunks)}개 청크\n")

    if not all_chunks:
        raise RuntimeError("청킹된 데이터가 없습니다.")

    embedder = _make_embedder()
    Path(QDRANT_PATH).mkdir(parents=True, exist_ok=True)
    qdrant_client = QdrantClient(path=QDRANT_PATH)

    if force_recreate:
        try:
            qdrant_client.delete_collection(QDRANT_COLLECTION)
            print(f"🗑️  기존 컬렉션 삭제: {QDRANT_COLLECTION}")
        except Exception:
            pass

    existing = {c.name for c in qdrant_client.get_collections().collections}
    if QDRANT_COLLECTION not in existing:
        sample_embedding = embedder.embed_query(all_chunks[0].text)
        qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=len(sample_embedding), distance=Distance.COSINE),
        )
        print(f"✅ 컬렉션 생성: {QDRANT_COLLECTION} (dim={len(sample_embedding)})")

    print("\n💾 Qdrant에 저장 중...")
    documents = [_chunk_to_document(c) for c in all_chunks]
    vs = QdrantVectorStore(client=qdrant_client, collection_name=QDRANT_COLLECTION, embedding=embedder)
    vs.add_documents(documents)

    count = qdrant_client.get_collection(QDRANT_COLLECTION).points_count
    print(f"\n{'=' * 60}")
    print(f"🎉 완료! {count}개 벡터 저장됨")
    print(f"{'=' * 60}")
    return vs


def load_vectorstore() -> QdrantVectorStore:
    embedder = _make_embedder()
    qdrant_client = QdrantClient(path=QDRANT_PATH)
    return QdrantVectorStore(
        client=qdrant_client,
        collection_name=QDRANT_COLLECTION,
        embedding=embedder,
    )
