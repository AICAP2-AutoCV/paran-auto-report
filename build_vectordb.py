#!/usr/bin/env python3
"""
Notion API → 임베딩 → Qdrant Vector DB 빌더

환경변수 설정 (.env):
    NOTION_TOKEN       = Notion Integration Secret
    NOTION_DATABASE_ID = 임베딩할 Notion 데이터베이스 ID
    OPENAI_API_KEY     = OpenAI API 키 (또는 OpenRouter 키)
    OPENAI_BASE_URL    = API 베이스 URL (기본: https://api.openai.com/v1)
    EMBEDDING_MODEL    = 임베딩 모델명 (기본: text-embedding-3-small)
    QDRANT_PATH        = Qdrant 로컬 저장 경로 (기본: ./qdrant_data)
    QDRANT_COLLECTION  = 컬렉션 이름 (기본: notion_docs)
"""

import os
import re
import time
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from dotenv import load_dotenv
from notion_client import Client as NotionClient
from openai import OpenAI
from langchain_core.documents import Document
from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
    MarkdownHeaderTextSplitter,
)
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
)
from langchain_qdrant import QdrantVectorStore
from langchain_core.embeddings import Embeddings

load_dotenv()

# ============================================================================
# 설정
# ============================================================================
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
QDRANT_PATH = os.getenv("QDRANT_PATH", "./qdrant_data")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "notion_docs")

CHUNK_SIZE = 800  # 토큰 기준
CHUNK_OVERLAP = 50
BATCH_SIZE = 50  # 임베딩 배치 크기


# ============================================================================
# 데이터 구조
# ============================================================================
@dataclass
class PageChunk:
    chunk_id: str
    page_id: str
    page_title: str
    section_title: str
    text: str
    properties: dict = field(default_factory=dict)
    created_time: str = ""
    last_edited_time: str = ""


# ============================================================================
# 1. Notion 데이터 수집
# ============================================================================
class NotionCollector:
    """Notion 데이터베이스에서 페이지 수집"""

    def __init__(self, token: str, database_id: str):
        if not token:
            raise ValueError("NOTION_TOKEN이 필요합니다.")
        if not database_id:
            raise ValueError("NOTION_DATABASE_ID가 필요합니다.")
        self.client = NotionClient(auth=token)
        self.database_id = database_id

    # ── 블록 재귀 수집 ──────────────────────────────────────────────────────

    def get_all_blocks(self, block_id: str) -> List[Dict]:
        """페이지네이션을 포함한 블록 전체 재귀 수집"""
        blocks = []
        cursor = None
        while True:
            resp = self.client.blocks.children.list(
                block_id=block_id,
                start_cursor=cursor,
                page_size=100,
            )
            blocks.extend(resp["results"])
            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]

        for block in blocks:
            if block.get("has_children"):
                block["children"] = self.get_all_blocks(block["id"])

        return blocks

    # ── 텍스트 추출 헬퍼 ────────────────────────────────────────────────────

    @staticmethod
    def _rich_text(rich_text_list: List) -> str:
        return "".join(t.get("plain_text", "") for t in (rich_text_list or []))

    def _block_to_text(self, block: Dict, depth: int = 0) -> str:
        """블록 하나를 마크다운 텍스트로 변환"""
        btype = block["type"]
        indent = "  " * depth
        result = ""

        text_map = {
            "paragraph": "",
            "heading_1": "# ",
            "heading_2": "## ",
            "heading_3": "### ",
            "bulleted_list_item": "• ",
            "numbered_list_item": "1. ",
            "quote": "> ",
            "callout": "💡 ",
            "toggle": "",
            "to_do": "",
        }

        if btype in text_map:
            raw = self._rich_text(block[btype].get("rich_text", []))
            if btype == "to_do":
                checked = "✅" if block["to_do"].get("checked") else "⬜"
                result = f"{indent}{checked} {raw}"
            else:
                result = f"{indent}{text_map[btype]}{raw}"

        elif btype == "code":
            code = self._rich_text(block["code"].get("rich_text", []))
            lang = block["code"].get("language", "")
            result = f"{indent}```{lang}\n{code}\n{indent}```"

        elif btype == "image":
            img = block["image"]
            url = (img.get("file") or img.get("external") or {}).get("url", "")
            caption = self._rich_text(img.get("caption", []))
            result = f"{indent}[Image: {url}]" + (f" - {caption}" if caption else "")

        elif btype == "table_row":
            cells = block["table_row"].get("cells", [])
            result = (
                indent + "| " + " | ".join(self._rich_text(c) for c in cells) + " |"
            )

        elif btype == "divider":
            result = f"{indent}---"

        elif btype in ("column_list", "column", "synced_block", "table"):
            result = ""  # 하위 블록만 처리

        else:
            result = f"{indent}[{btype}]"

        # 하위 블록 재귀 처리
        for child in block.get("children", []):
            child_text = self._block_to_text(child, depth + 1)
            if child_text:
                result += "\n" + child_text

        return result

    # ── 속성 추출 ──────────────────────────────────────────────────────────

    def _extract_properties(self, page: Dict) -> Dict:
        """페이지 속성(메타데이터) 추출"""
        result = {}
        for name, prop in page.get("properties", {}).items():
            ptype = prop["type"]
            try:
                if ptype == "title":
                    result[name] = self._rich_text(prop["title"])
                elif ptype == "rich_text":
                    result[name] = self._rich_text(prop["rich_text"])
                elif ptype == "number":
                    result[name] = prop["number"]
                elif ptype == "select":
                    result[name] = prop["select"]["name"] if prop["select"] else None
                elif ptype == "multi_select":
                    result[name] = [s["name"] for s in prop["multi_select"]]
                elif ptype == "date":
                    result[name] = prop["date"] or {}
                elif ptype == "checkbox":
                    result[name] = prop["checkbox"]
                elif ptype == "url":
                    result[name] = prop["url"]
                elif ptype in ("created_time", "last_edited_time"):
                    result[name] = prop[ptype]
                elif ptype == "status":
                    result[name] = prop["status"]["name"] if prop["status"] else None
                elif ptype == "relation":
                    result[name] = [r["id"] for r in prop["relation"]]
                else:
                    result[name] = f"[{ptype}]"
            except Exception:
                result[name] = None
        return result

    @staticmethod
    def _get_title(properties: Dict) -> str:
        """속성에서 페이지 제목 추출"""
        for key in ("title", "Name", "이름", "제목"):
            if key in properties and isinstance(properties[key], str):
                return properties[key]
        for v in properties.values():
            if isinstance(v, str) and v:
                return v
        return "Untitled"

    # ── 전체 수집 ──────────────────────────────────────────────────────────

    def collect_all(self, limit: Optional[int] = None) -> List[Dict]:
        """데이터베이스의 모든 페이지 수집"""
        print(f"📥 Notion 데이터베이스 수집 시작: {self.database_id}")
        pages = []
        cursor = None
        while True:
            resp = self.client.databases.query(
                database_id=self.database_id,
                start_cursor=cursor,
                page_size=100,
            )
            pages.extend(resp["results"])
            print(f"  페이지 수집: {len(pages)}개...")
            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]

        if limit:
            pages = pages[:limit]

        print(f"✅ 총 {len(pages)}개 페이지 발견\n")

        all_data = []
        for idx, page in enumerate(pages):
            page_id = page["id"]
            properties = self._extract_properties(page)
            title = self._get_title(properties)
            print(f"[{idx + 1}/{len(pages)}] {title}")

            try:
                blocks = self.get_all_blocks(page_id)
                lines = [self._block_to_text(b) for b in blocks]
                content = "\n".join(line for line in lines if line)
                print(f"  → 블록 {len(blocks)}개, {len(content)}자")
            except Exception as e:
                print(f"  ⚠️  블록 수집 실패: {e}")
                content = ""

            all_data.append(
                {
                    "page_id": page_id,
                    "title": title,
                    "content": content,
                    "properties": properties,
                    "created_time": page.get("created_time", ""),
                    "last_edited_time": page.get("last_edited_time", ""),
                }
            )

        print(f"\n🎉 수집 완료: {len(all_data)}개 페이지")
        return all_data


# ============================================================================
# 2. 청킹
# ============================================================================
def chunk_page(page: Dict) -> List[PageChunk]:
    """페이지 데이터를 청크 리스트로 변환"""
    page_id = page["page_id"]
    page_title = page.get("title", "Untitled")
    content = page.get("content", "").strip()

    if not content:
        return []

    # 마크다운 헤더 기준으로 1차 분할
    headers_to_split = [
        ("#", "h1"),
        ("##", "h2"),
        ("###", "h3"),
    ]
    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=headers_to_split,
        strip_headers=False,
    )
    sections = md_splitter.split_text(content)

    # 섹션별 재귀 분할
    char_chunk_size = CHUNK_SIZE * 4  # 토큰 → 문자 환산
    char_overlap = CHUNK_OVERLAP * 4
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=char_chunk_size,
        chunk_overlap=char_overlap,
        separators=["\n## ", "\n### ", "\n\n", "\n• ", "\n", ". ", " ", ""],
    )

    chunks: List[PageChunk] = []
    for sec_idx, section in enumerate(sections):
        section_title = (
            section.metadata.get("h1")
            or section.metadata.get("h2")
            or section.metadata.get("h3")
            or page_title
        )
        sub_texts = text_splitter.split_text(section.page_content)

        for chunk_idx, text in enumerate(sub_texts):
            text = text.strip()
            if not text:
                continue
            chunks.append(
                PageChunk(
                    chunk_id=f"{page_id}_{sec_idx}_{chunk_idx}",
                    page_id=page_id,
                    page_title=page_title,
                    section_title=section_title,
                    text=text,
                    properties=page.get("properties", {}),
                    created_time=page.get("created_time", ""),
                    last_edited_time=page.get("last_edited_time", ""),
                )
            )

    return chunks


# ============================================================================
# 3. 임베딩
# ============================================================================
class OpenAIEmbedder(Embeddings):
    """OpenAI / OpenRouter 호환 임베딩 클라이언트"""

    def __init__(
        self, api_key: str, model: str, base_url: str, batch_size: int = BATCH_SIZE
    ):
        if not api_key:
            raise ValueError("OPENAI_API_KEY가 필요합니다.")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.batch_size = batch_size

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t.strip() or " " for t in texts[i : i + self.batch_size]]
            for attempt in range(3):
                try:
                    resp = self.client.embeddings.create(model=self.model, input=batch)
                    all_embeddings.extend(d.embedding for d in resp.data)
                    print(
                        f"  임베딩: {min(i + self.batch_size, len(texts))}/{len(texts)}"
                    )
                    time.sleep(0.3)
                    break
                except Exception as e:
                    wait = (attempt + 1) * 2
                    print(f"  ⚠️  재시도 {attempt + 1}/3 ({wait}s 대기): {e}")
                    if attempt < 2:
                        time.sleep(wait)
                    else:
                        raise
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        resp = self.client.embeddings.create(
            model=self.model, input=[text.strip() or " "]
        )
        return resp.data[0].embedding


# ============================================================================
# 4. Vector DB 구축
# ============================================================================
def chunk_to_document(chunk: PageChunk) -> Document:
    """PageChunk → LangChain Document 변환"""
    metadata = {
        "chunk_id": chunk.chunk_id,
        "page_id": chunk.page_id,
        "page_title": chunk.page_title,
        "section_title": chunk.section_title,
        "created_time": chunk.created_time,
        "last_edited_time": chunk.last_edited_time,
    }
    # 직렬화 가능한 속성만 메타데이터에 추가
    for k, v in chunk.properties.items():
        if isinstance(v, (str, int, float, bool)):
            metadata[f"prop_{k}"] = v
        elif isinstance(v, list):
            metadata[f"prop_{k}"] = ", ".join(str(i) for i in v)

    return Document(page_content=chunk.text, metadata=metadata)


def build_vectordb(
    force_recreate: bool = False,
    limit: Optional[int] = None,
) -> QdrantVectorStore:
    """
    Notion 데이터 수집 → 청킹 → 임베딩 → Qdrant 저장

    Args:
        force_recreate: True면 컬렉션 전체 재생성
        limit:          수집할 페이지 수 제한
    Returns:
        구축된 QdrantVectorStore 인스턴스
    """
    print("=" * 60)
    print("🚀 Vector DB 구축 시작")
    print(f"   모델: {EMBEDDING_MODEL}")
    print(f"   컬렉션: {QDRANT_COLLECTION}")
    print(f"   저장 경로: {QDRANT_PATH}")
    print("=" * 60)

    # 1. Notion 수집
    collector = NotionCollector(NOTION_TOKEN, NOTION_DATABASE_ID)
    pages = collector.collect_all(limit=limit)

    # 2. 청킹
    print("\n✂️  청킹 중...")
    all_chunks: List[PageChunk] = []
    for page in pages:
        chunks = chunk_page(page)
        all_chunks.extend(chunks)
        print(f"  {page['title']}: {len(chunks)}개 청크")
    print(f"✅ 총 {len(all_chunks)}개 청크 생성\n")

    if not all_chunks:
        raise RuntimeError("청킹된 데이터가 없습니다. Notion 페이지 내용을 확인하세요.")

    # 3. 임베딩 클라이언트
    embedder = OpenAIEmbedder(
        api_key=OPENAI_API_KEY,
        model=EMBEDDING_MODEL,
        base_url=OPENAI_BASE_URL,
    )

    # 4. Qdrant 초기화
    Path(QDRANT_PATH).mkdir(parents=True, exist_ok=True)
    qdrant_client = QdrantClient(path=QDRANT_PATH)

    if force_recreate:
        try:
            qdrant_client.delete_collection(QDRANT_COLLECTION)
            print(f"🗑️  기존 컬렉션 삭제: {QDRANT_COLLECTION}")
        except Exception:
            pass

    # 컬렉션 존재 여부 확인
    existing = {c.name for c in qdrant_client.get_collections().collections}
    if QDRANT_COLLECTION not in existing:
        # 벡터 차원 확인
        sample_embedding = embedder.embed_query(all_chunks[0].text)
        vector_dim = len(sample_embedding)
        print(f"📏 벡터 차원: {vector_dim}")

        qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=VectorParams(size=vector_dim, distance=Distance.COSINE),
        )
        print(f"✅ 컬렉션 생성: {QDRANT_COLLECTION}")

    # 5. 문서 변환 및 저장
    print("\n💾 Qdrant에 저장 중...")
    documents = [chunk_to_document(c) for c in all_chunks]

    vectorstore = QdrantVectorStore(
        client=qdrant_client,
        collection_name=QDRANT_COLLECTION,
        embedding=embedder,
    )
    vectorstore.add_documents(documents)

    count = qdrant_client.get_collection(QDRANT_COLLECTION).points_count
    print(f"\n{'=' * 60}")
    print(f"🎉 완료! Qdrant에 {count}개 벡터 저장됨")
    print(f"{'=' * 60}")

    return vectorstore


# ============================================================================
# 5. 검색 유틸리티
# ============================================================================
def load_vectorstore() -> QdrantVectorStore:
    """기존에 저장된 Qdrant 벡터스토어 로드"""
    embedder = OpenAIEmbedder(
        api_key=OPENAI_API_KEY,
        model=EMBEDDING_MODEL,
        base_url=OPENAI_BASE_URL,
    )
    qdrant_client = QdrantClient(path=QDRANT_PATH)
    return QdrantVectorStore(
        client=qdrant_client,
        collection_name=QDRANT_COLLECTION,
        embedding=embedder,
    )


def search(query: str, k: int = 5) -> List[Document]:
    """벡터 유사도 검색"""
    vs = load_vectorstore()
    results = vs.similarity_search(query, k=k)
    for i, doc in enumerate(results, 1):
        title = doc.metadata.get("page_title", "")
        section = doc.metadata.get("section_title", "")
        print(f"\n[{i}] {title} > {section}")
        print(f"    {doc.page_content[:200]}...")
    return results


# ============================================================================
# CLI
# ============================================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Notion → Qdrant Vector DB 빌더")
    sub = parser.add_subparsers(dest="command")

    # build 서브커맨드
    build_cmd = sub.add_parser("build", help="Vector DB 구축")
    build_cmd.add_argument("--force", action="store_true", help="컬렉션 전체 재생성")
    build_cmd.add_argument(
        "--limit", type=int, default=None, help="수집할 페이지 수 제한"
    )

    # search 서브커맨드
    search_cmd = sub.add_parser("search", help="Vector DB 검색 테스트")
    search_cmd.add_argument("query", help="검색 쿼리")
    search_cmd.add_argument("--k", type=int, default=5, help="반환할 결과 수")

    args = parser.parse_args()

    if args.command == "build":
        build_vectordb(force_recreate=args.force, limit=args.limit)
    elif args.command == "search":
        search(args.query, k=args.k)
    else:
        parser.print_help()
