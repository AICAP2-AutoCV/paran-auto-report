"""Qdrant 벡터스토어 구축 및 로드"""

from pathlib import Path
from typing import Optional, List

from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from langchain_qdrant import QdrantVectorStore

from ..config import (
    NOTION_TOKEN, NOTION_DATABASE_ID,
    OPENAI_API_KEY, OPENAI_BASE_URL, EMBEDDING_MODEL,
    QDRANT_PATH, QDRANT_COLLECTION,
)
from .models import PageChunk
from .notion import NotionCollector
from .chunker import chunk_page
from .embedder import OpenAIEmbedder


def _chunk_to_document(chunk: PageChunk) -> Document:
    metadata = {
        "chunk_id": chunk.chunk_id,
        "page_id": chunk.page_id,
        "page_title": chunk.page_title,
        "section_title": chunk.section_title,
        "created_time": chunk.created_time,
        "last_edited_time": chunk.last_edited_time,
    }
    for k, v in chunk.properties.items():
        if isinstance(v, (str, int, float, bool)):
            metadata[f"prop_{k}"] = v
        elif isinstance(v, list):
            metadata[f"prop_{k}"] = ", ".join(str(i) for i in v)
    return Document(page_content=chunk.text, metadata=metadata)


def _make_embedder() -> OpenAIEmbedder:
    return OpenAIEmbedder(
        api_key=OPENAI_API_KEY,
        model=EMBEDDING_MODEL,
        base_url=OPENAI_BASE_URL,
    )


def build_vectordb(force_recreate: bool = False, limit: Optional[int] = None) -> QdrantVectorStore:
    print("=" * 60)
    print("🚀 Vector DB 구축 시작")
    print(f"   모델: {EMBEDDING_MODEL}")
    print(f"   컬렉션: {QDRANT_COLLECTION}")
    print(f"   경로: {QDRANT_PATH}")
    print("=" * 60)

    collector = NotionCollector(NOTION_TOKEN, NOTION_DATABASE_ID)
    pages = collector.collect_all(limit=limit)

    print("\n✂️  청킹 중...")
    all_chunks: List[PageChunk] = []
    for page in pages:
        chunks = chunk_page(page)
        all_chunks.extend(chunks)
        print(f"  {page['title']}: {len(chunks)}개 청크")
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
