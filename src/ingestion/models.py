from dataclasses import dataclass, field


@dataclass
class PageChunk:
    chunk_id: str
    page_id: str
    page_title: str
    section_title: str
    text: str
    combined_text: str = ""
    has_image: bool = False
    image_paths: list = field(default_factory=list)
    image_descriptions: list = field(default_factory=list)
    properties: dict = field(default_factory=dict)
    images: list = field(default_factory=list)
    created_time: str = ""
    last_edited_time: str = ""
