"""텍스트 청킹"""

from typing import List, Dict

from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from ..config import CHUNK_SIZE, CHUNK_OVERLAP
from .models import PageChunk


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
