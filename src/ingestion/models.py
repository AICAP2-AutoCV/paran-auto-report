from dataclasses import dataclass, field


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
