"""텍스트 청킹"""

import re
from typing import List, Dict

from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownHeaderTextSplitter

from ..config import CHUNK_SIZE, CHUNK_OVERLAP
from .models import PageChunk


def _find_images_in_text(text: str) -> List[Dict]:
    pattern = r'\[Image:\s*([^\]]+)\]'
    images = []
    for match in re.finditer(pattern, text):
        path = match.group(1).strip()
        if path and not path.startswith(("http://", "https://", "/", "notion_images/")):
            path = f"notion_images/{path}"
        images.append({
            "path": path,
            "start": match.start(),
            "end": match.end(),
            "marker": match.group(0),
        })
    return images


def _describe_chunk_images(chunk_text: str, chunk_images: List[Dict], page_title: str, section_title: str, vision_model) -> List[str]:
    descriptions = []
    for image in chunk_images:
        if vision_model is None:
            descriptions.append("")
            continue
        context = {
            "page_title": page_title,
            "section_title": section_title,
            "text_before": chunk_text[max(0, image["start"] - 200):image["start"]],
            "text_after": chunk_text[image["end"]:image["end"] + 200],
        }
        descriptions.append(vision_model.describe_image(image["path"], context))
    return descriptions


def chunk_page(page: Dict, vision_model=None) -> List[PageChunk]:
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
            chunk_images = _find_images_in_text(text)
            image_paths = [image["path"] for image in chunk_images]
            image_descriptions = _describe_chunk_images(
                text,
                chunk_images,
                page_title,
                section_title,
                vision_model,
            )
            combined_text = text
            for image, description in zip(chunk_images, image_descriptions):
                replacement = f"[이미지: {description}]" if description else image["marker"]
                combined_text = combined_text.replace(image["marker"], replacement)
            chunks.append(PageChunk(
                chunk_id=f"{page_id}_{sec_idx}_{chunk_idx}",
                page_id=page_id,
                page_title=page_title,
                section_title=section_title,
                text=text,
                combined_text=combined_text,
                has_image=bool(chunk_images),
                image_paths=image_paths,
                image_descriptions=image_descriptions,
                properties=page.get("properties", {}),
                images=page.get("images", []),
                created_time=page.get("created_time", ""),
                last_edited_time=page.get("last_edited_time", ""),
            ))
    return chunks
