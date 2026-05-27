"""reference .docx 파일에서 평문 텍스트를 추출한다."""

from pathlib import Path
from docx import Document as DocxDocument


def extract_text(docx_path: str | Path) -> str:
    """
    .docx 파일의 텍스트를 추출한다.
    - 단락(paragraph): 순서대로 읽음
    - 표(table): 셀 텍스트를 '|'로 구분해 읽음
    반환: 줄바꿈으로 이어진 단일 문자열
    """
    doc = DocxDocument(str(docx_path))
    lines: list[str] = []

    for block in _iter_block_items(doc):
        if block["type"] == "paragraph":
            text = block["text"].strip()
            if text:
                lines.append(text)
        elif block["type"] == "table":
            for row in block["rows"]:
                row_text = " | ".join(cell.strip() for cell in row if cell.strip())
                if row_text:
                    lines.append(row_text)

    return "\n".join(lines)


def _iter_block_items(doc: DocxDocument):
    """document.element의 body를 순서대로 순회해 단락/표를 yield."""
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    parent = doc.element.body
    for child in parent.iterchildren():
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "p":
            para = Paragraph(child, doc)
            yield {"type": "paragraph", "text": para.text}
        elif tag == "tbl":
            table = Table(child, doc)
            rows = []
            for row in table.rows:
                cells = [cell.text for cell in row.cells]
                rows.append(cells)
            yield {"type": "table", "rows": rows}
