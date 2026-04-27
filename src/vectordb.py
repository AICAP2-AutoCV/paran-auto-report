"""
Notion → 청킹 → 임베딩 → Qdrant Vector DB
"""

import re
import time
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field

from notion_client import Client as NotionClient
from openai import OpenAI
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from langchain_qdrant import QdrantVectorStore

from .config import (
    NOTION_TOKEN, NOTION_DATABASE_ID,
    OPENAI_API_KEY, OPENAI_BASE_URL, EMBEDDING_MODEL,
    QDRANT_PATH, QDRANT_COLLECTION,
    CHUNK_SIZE, CHUNK_OVERLAP, EMBED_BATCH_SIZE,
)


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
# 1. Notion 수집
# ============================================================================
class NotionCollector:
    def __init__(self, token: str, database_id: str):
        if not token:
            raise ValueError("NOTION_TOKEN이 필요합니다.")
        if not database_id:
            raise ValueError("NOTION_DATABASE_ID 또는 DATA_SOURCE_ID가 필요합니다.")
        self.client = NotionClient(auth=token)
        self.database_id = database_id

    def get_all_blocks(self, block_id: str) -> List[Dict]:
        blocks, cursor = [], None
        while True:
            resp = self.client.blocks.children.list(
                block_id=block_id, start_cursor=cursor, page_size=100
            )
            blocks.extend(resp["results"])
            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]
        for block in blocks:
            if block.get("has_children"):
                block["children"] = self.get_all_blocks(block["id"])
        return blocks

    @staticmethod
    def _rich_text(rich_text_list: List) -> str:
        return "".join(t.get("plain_text", "") for t in (rich_text_list or []))

    def _block_to_text(self, block: Dict, depth: int = 0) -> str:
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
            result = indent + "| " + " | ".join(self._rich_text(c) for c in cells) + " |"
        elif btype == "divider":
            result = f"{indent}---"
        elif btype in ("column_list", "column", "synced_block", "table"):
            result = ""
        else:
            result = f"{indent}[{btype}]"

        for child in block.get("children", []):
            child_text = self._block_to_text(child, depth + 1)
            if child_text:
                result += "\n" + child_text
        return result

    def _extract_properties(self, page: Dict) -> Dict:
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
        for key in ("title", "Name", "이름", "제목"):
            if key in properties and isinstance(properties[key], str):
                return properties[key]
        for v in properties.values():
            if isinstance(v, str) and v:
                return v
        return "Untitled"

    def collect_all(self, limit: Optional[int] = None) -> List[Dict]:
        print(f"📥 Notion 수집 시작: {self.database_id}")
        pages, cursor = [], None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = self.client.data_sources.query(self.database_id, **kwargs)
            pages.extend(resp["results"])
            print(f"  수집: {len(pages)}개...")
            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]

        if limit:
            pages = pages[:limit]
        print(f"✅ 총 {len(pages)}개 페이지\n")

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

            all_data.append({
                "page_id": page_id,
                "title": title,
                "content": content,
                "properties": properties,
                "created_time": page.get("created_time", ""),
                "last_edited_time": page.get("last_edited_time", ""),
            })

        print(f"\n🎉 수집 완료: {len(all_data)}개")
        return all_data


# ============================================================================
# 2. 청킹
# ============================================================================
def chunk_page(page: Dict) -> List[PageChunk]:
    page_id = page["page_id"]
    page_title = page.get("title", "Untitled")
    content = page.get("content", "").strip()
    if not content:
        return []

    md_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=[("#", "h1"), ("##", "h2"), ("###", "h3")],
        strip_headers=False,
    )
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE * 4,
        chunk_overlap=CHUNK_OVERLAP * 4,
        separators=["\n## ", "\n### ", "\n\n", "\n• ", "\n", ". ", " ", ""],
    )

    chunks: List[PageChunk] = []
    for sec_idx, section in enumerate(md_splitter.split_text(content)):
        section_title = (
            section.metadata.get("h1")
            or section.metadata.get("h2")
            or section.metadata.get("h3")
            or page_title
        )
        for chunk_idx, text in enumerate(text_splitter.split_text(section.page_content)):
            text = text.strip()
            if not text:
                continue
            chunks.append(PageChunk(
                chunk_id=f"{page_id}_{sec_idx}_{chunk_idx}",
                page_id=page_id,
                page_title=page_title,
                section_title=section_title,
                text=text,
                properties=page.get("properties", {}),
                created_time=page.get("created_time", ""),
                last_edited_time=page.get("last_edited_time", ""),
            ))
    return chunks


# ============================================================================
# 3. 임베딩
# ============================================================================
class OpenAIEmbedder(Embeddings):
    def __init__(self, api_key: str, model: str, base_url: str, batch_size: int = EMBED_BATCH_SIZE):
        if not api_key:
            raise ValueError("OPENAI_API_KEY 또는 OPENROUTER_API_KEY가 필요합니다.")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.batch_size = batch_size

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        all_embeddings = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t.strip() or " " for t in texts[i: i + self.batch_size]]
            for attempt in range(3):
                try:
                    resp = self.client.embeddings.create(model=self.model, input=batch)
                    all_embeddings.extend(d.embedding for d in resp.data)
                    print(f"  임베딩: {min(i + self.batch_size, len(texts))}/{len(texts)}")
                    time.sleep(0.3)
                    break
                except Exception as e:
                    wait = (attempt + 1) * 2
                    print(f"  ⚠️  재시도 {attempt + 1}/3 ({wait}s): {e}")
                    if attempt < 2:
                        time.sleep(wait)
                    else:
                        raise
        return all_embeddings

    def embed_query(self, text: str) -> List[float]:
        resp = self.client.embeddings.create(model=self.model, input=[text.strip() or " "])
        return resp.data[0].embedding


# ============================================================================
# 4. Vector DB 구축 / 로드
# ============================================================================
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
