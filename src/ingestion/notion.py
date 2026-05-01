"""Notion 데이터베이스 수집기"""

from typing import List, Dict, Optional

from notion_client import Client as NotionClient

from .models import PageChunk


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
