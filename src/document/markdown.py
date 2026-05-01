"""마크다운 텍스트 전처리 (순수 함수, docx 의존성 없음)"""

import re
from typing import List, Tuple


def parse_markdown_table(table_text: str) -> List[List[str]]:
    """마크다운 테이블 파싱 → 2D 리스트 (행x열)"""
    lines = table_text.strip().split('\n')
    rows = []

    for line in lines:
        if not line.strip():
            continue
        if '|' not in line:
            continue

        # 구분선 스킵 (|---|---| 패턴)
        temp_cells = [cell.strip() for cell in line.split('|') if cell.strip()]
        if temp_cells and all(set(cell) <= set('-: ') for cell in temp_cells):
            continue

        parts = line.split('|')
        if parts and not parts[0].strip():
            parts = parts[1:]
        if parts and not parts[-1].strip():
            parts = parts[:-1]

        cells = [cell.strip() for cell in parts]
        if cells:
            rows.append(cells)

    return rows


def _finalize_table_buffer(table_buffer: list, output_lines: list):
    """테이블 버퍼를 정리하고 출력 라인에 추가 (구분선 없으면 생성)"""
    if not table_buffer:
        return

    header = table_buffer[0]
    has_separator = False
    separator_idx = -1
    data_start_idx = 1

    for idx in range(1, min(3, len(table_buffer))):
        line = table_buffer[idx].strip()
        if re.match(r'^\|[\s\-:|]+\|$', line) and '-' in line:
            has_separator = True
            separator_idx = idx
            data_start_idx = idx + 1
            break

    output_lines.append(header)

    if has_separator:
        output_lines.append(table_buffer[separator_idx])
    else:
        num_cols = header.count('|') - 1
        separator = '|' + '|'.join(['---' for _ in range(num_cols)]) + '|'
        output_lines.append(separator)

    for row in table_buffer[data_start_idx:]:
        output_lines.append(row)


def fix_table_format(text: str) -> str:
    """유니코드 박스 문자를 마크다운 테이블로 변환하고, 리스트 안 테이블을 독립 블록으로 정리"""
    text = text.replace('│', '|').replace('─', '-')

    lines = text.split('\n')
    fixed_lines = []
    in_table = False
    table_buffer = []

    for line in lines:
        stripped = line.strip()
        is_table_line = False
        clean_line = line

        if re.match(r'^\s*[-*+]\s+\|', line):
            is_table_line = True
            clean_line = re.sub(r'^\s*[-*+]\s+', '', line)
        elif re.match(r'^\s+\|', line):
            is_table_line = True
            clean_line = stripped
        elif stripped.startswith('|') and stripped.endswith('|'):
            is_table_line = True
            clean_line = stripped

        if is_table_line:
            if not in_table:
                in_table = True
                if fixed_lines and fixed_lines[-1].strip():
                    fixed_lines.append('')
            table_buffer.append(clean_line)
        else:
            if in_table:
                _finalize_table_buffer(table_buffer, fixed_lines)
                table_buffer = []
                in_table = False
                fixed_lines.append('')
            fixed_lines.append(line)

    if in_table and table_buffer:
        if fixed_lines and fixed_lines[-1].strip():
            fixed_lines.append('')
        _finalize_table_buffer(table_buffer, fixed_lines)
        fixed_lines.append('')

    return '\n'.join(fixed_lines)


def normalize_list_indentation(text: str) -> str:
    """마크다운 리스트 들여쓰기를 pandoc이 인식할 수 있도록 4칸 단위로 정규화"""
    lines = text.split('\n')
    result_lines = []

    for line in lines:
        if not line.strip():
            result_lines.append(line)
            continue

        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        is_list_item = bool(re.match(r'^[-*+]\s', stripped) or re.match(r'^\d+\.\s', stripped))

        if is_list_item and indent > 0:
            level = (indent - 1) // 4 + 1
            normalized_indent = level * 4
            result_lines.append(' ' * normalized_indent + stripped)
        else:
            result_lines.append(line)

    return '\n'.join(result_lines)


def extract_title_tag(text: str) -> Tuple[str | None, str]:
    """[TITLE]...[/TITLE] 태그에서 제목과 본문을 분리."""
    title_match = re.search(r'\[TITLE\](.*?)\[/TITLE\]', text, flags=re.DOTALL)
    if not title_match:
        return None, text

    title = re.sub(r'\s+', ' ', title_match.group(1)).strip()
    body = re.sub(r'\[TITLE\].*?\[/TITLE\]\s*', '', text, count=1, flags=re.DOTALL).lstrip()
    return title or None, body


def normalize_title_tag(text: str) -> str:
    """[TITLE]...[/TITLE] 태그를 문서용 H1 헤딩으로 변환."""
    title, body = extract_title_tag(text)
    if not title:
        return body
    return f"# {title}\n\n{body}"


def remove_first_heading(text: str) -> str:
    """불필요한 최상위 헤딩 및 Executive Summary 섹션 제거, 섹션 레벨 정규화"""
    lines = text.split('\n')
    result_lines = []
    skip_mode = False

    for line in lines:
        stripped = line.strip()

        if re.match(r'^#{1,3}\s*(임원\s*보고서|최종\s*보고서|일주일\s*보고서|주간\s*보고서)', stripped):
            continue

        if re.match(r'^#{1,3}\s*Executive Summary', stripped, re.IGNORECASE):
            skip_mode = True
            continue

        if skip_mode:
            if re.match(r'^#{1,3}\s*\d+\.', stripped):
                skip_mode = False
            else:
                continue

        # 번호 붙은 섹션을 ## 레벨로 통일 (### 1. ... → ## 1. ...)
        num_section = re.match(r'^#{1,4}\s*(\d+\..+)$', stripped)
        if num_section:
            result_lines.append(f'## {num_section.group(1)}')
            continue

        result_lines.append(line)

    return '\n'.join(result_lines)


def replace_tables_with_placeholders(text: str, start_index: int = 0) -> Tuple[str, List[str]]:
    """마크다운 텍스트에서 표를 [TABLE_N] placeholder로 치환

    Returns:
        (치환된 텍스트, 추출된 표 리스트)
    """
    lines = text.split('\n')
    result_lines = []
    tables = []
    i = 0
    table_index = start_index

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith('|') and '|' in line:
            table_lines = []
            while i < len(lines):
                current_line = lines[i].strip()
                if current_line.startswith('|') and '|' in current_line:
                    table_lines.append(current_line)
                    i += 1
                else:
                    break

            if table_lines:
                tables.append('\n'.join(table_lines))
                result_lines.append(f'[TABLE_{table_index}]')
                table_index += 1
        else:
            result_lines.append(line)
            i += 1

    return '\n'.join(result_lines), tables


def extract_tables_from_markdown(text: str) -> List[str]:
    """마크다운 텍스트에서 테이블 추출"""
    tables = []
    lines = text.split('\n')
    i = 0

    while i < len(lines):
        line = lines[i]

        if line.strip().startswith('|') and '|' in line:
            table_lines = []
            while i < len(lines):
                current_line = lines[i].strip()
                if current_line.startswith('|') and '|' in current_line:
                    table_lines.append(current_line)
                    i += 1
                else:
                    break

            if table_lines:
                tables.append('\n'.join(table_lines))
        else:
            i += 1

    return tables
