#!/usr/bin/env python3
"""
임베딩별 Qdrant 사전 빌드 스크립트 (1회성).

사용법:
  cd paran-auto-report
  python -m experiments.setup_vectordbs --embeddings qwen3-8b gemini-emb-001
  python -m experiments.setup_vectordbs --embeddings qwen3-8b --force-recreate
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from langchain_qdrant import QdrantVectorStore

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OPENAI_API_KEY, OPENAI_BASE_URL
from src.ingestion.embedder import OpenAIEmbedder
from src.ingestion.chunker import chunk_page
from src.ingestion.store import _collect_all_pages, _chunk_to_document
from src.ingestion.vision import get_vision_describer

CONFIG_PATH = Path(__file__).resolve().parent / "eval_config.yaml"
BASE_DIR = Path(__file__).resolve().parent.parent


def load_embedding_configs() -> list[dict]:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return [e for e in cfg["embeddings"] if e.get("qdrant_path")]


def build_for_embedding(emb_cfg: dict, force_recreate: bool = False) -> None:
    name = emb_cfg["name"]
    model = emb_cfg["model"]
    qdrant_path = str(BASE_DIR / emb_cfg["qdrant_path"])
    collection = emb_cfg.get("collection", "notion_docs")

    print(f"\n{'=' * 60}")
    print(f"🔧 [{name}] 빌드 시작")
    print(f"   모델:     {model}")
    print(f"   경로:     {qdrant_path}")
    print(f"   컬렉션:   {collection}")
    print(f"{'=' * 60}")

    Path(qdrant_path).mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=qdrant_path)

    existing = {c.name for c in client.get_collections().collections}
    if collection in existing and not force_recreate:
        count = client.get_collection(collection).points_count
        print(f"⏭️  [{name}] 이미 존재 (벡터 {count}개) — 스킵. --force-recreate로 재빌드 가능.")
        return

    if collection in existing and force_recreate:
        client.delete_collection(collection)
        print(f"🗑️  [{name}] 기존 컬렉션 삭제")

    print(f"\n📂 [{name}] Notion 페이지 수집 중...")
    pages = _collect_all_pages()
    print(f"   {len(pages)}개 페이지 수집 완료")

    print(f"\n🖼️  [{name}] Vision 모델 준비 중...")
    vision_model = get_vision_describer()

    print(f"\n✂️  [{name}] 청킹 중...")
    all_chunks = []
    for page in pages:
        chunks = chunk_page(page, vision_model=vision_model)
        all_chunks.extend(chunks)
    print(f"   총 {len(all_chunks)}개 청크")

    if not all_chunks:
        print(f"⚠️  [{name}] 청크 없음 — 스킵")
        return

    print(f"\n🔢 [{name}] 임베딩 생성 중...")
    embedder = OpenAIEmbedder(api_key=OPENAI_API_KEY, model=model, base_url=OPENAI_BASE_URL)

    sample_embedding = embedder.embed_query(all_chunks[0].text)
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=len(sample_embedding), distance=Distance.COSINE),
    )
    print(f"   컬렉션 생성 완료 (dim={len(sample_embedding)})")

    documents = [_chunk_to_document(c) for c in all_chunks]
    vs = QdrantVectorStore(client=client, collection_name=collection, embedding=embedder)
    vs.add_documents(documents)

    count = client.get_collection(collection).points_count
    print(f"\n🎉 [{name}] 완료 — {count}개 벡터 저장")


def main() -> None:
    parser = argparse.ArgumentParser(description="임베딩별 Qdrant 사전 빌드")
    parser.add_argument(
        "--embeddings", nargs="+", required=True,
        help="빌드할 embedding name 목록 (eval_config.yaml의 embeddings[].name)"
    )
    parser.add_argument(
        "--force-recreate", action="store_true",
        help="이미 존재하는 컬렉션도 재빌드"
    )
    args = parser.parse_args()

    all_configs = load_embedding_configs()
    name_to_cfg = {e["name"]: e for e in all_configs}

    for name in args.embeddings:
        if name not in name_to_cfg:
            available = list(name_to_cfg.keys())
            print(f"⚠️  '{name}' 는 eval_config.yaml에 없거나 qdrant_path가 null입니다.")
            print(f"   사용 가능: {available}")
            continue
        build_for_embedding(name_to_cfg[name], force_recreate=args.force_recreate)

    print("\n✅ 모든 빌드 완료")


if __name__ == "__main__":
    main()
