"""학과별 보고서 템플릿 로더

YAML 형태로 정의된 학과 템플릿을 불러와
RAG 프롬프트에 동적으로 반영할 수 있도록 처리합니다.
"""
from __future__ import annotations

import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional


TEMPLATES_DIR = Path(__file__).parent.parent / "config" / "department_templates"


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_departments() -> List[Dict[str, str]]:
    """사용 가능한 학과 목록 반환.

    Returns:
        [{"id": ..., "name": ..., "name_en": ..., "description": ...}, ...]
    """
    departments = []
    for yaml_file in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            data = _load_yaml(yaml_file)
            dept = data.get("department", {})
            departments.append({
                "id": dept.get("id", yaml_file.stem),
                "name": dept.get("name", ""),
                "name_en": dept.get("name_en", ""),
                "description": dept.get("description", ""),
            })
        except Exception:
            continue
    return departments


def load_department_template(department_id: str) -> Dict[str, Any]:
    """학과 ID로 템플릿을 로드합니다.

    Args:
        department_id: YAML 파일명(확장자 제외) 또는 department.id 값

    Raises:
        FileNotFoundError: 해당 학과 템플릿이 없을 때
    """
    yaml_path = TEMPLATES_DIR / f"{department_id}.yaml"
    if not yaml_path.exists():
        for f in TEMPLATES_DIR.glob("*.yaml"):
            data = _load_yaml(f)
            if data.get("department", {}).get("id") == department_id:
                return data
        available = [f.stem for f in TEMPLATES_DIR.glob("*.yaml")]
        raise FileNotFoundError(
            f"학과 템플릿 '{department_id}'를 찾을 수 없습니다. "
            f"사용 가능한 템플릿: {available}"
        )
    return _load_yaml(yaml_path)


def build_department_system_prompt(template: Dict[str, Any]) -> str:
    """학과 템플릿으로부터 시스템 프롬프트 조각을 생성합니다."""
    dept = template.get("department", {})
    tone = template.get("tone", {})
    emphasis_list = template.get("emphasis", [])

    lines = [
        f"당신은 **{dept.get('name', '')}** 보고서 작성 전문가입니다.",
        "",
        "[학과 특성]",
        dept.get("description", ""),
        "",
        "[작성 톤 & 스타일]",
        tone.get("description", ""),
        "",
        "[강조 사항]",
    ]
    for item in emphasis_list:
        lines.append(f"- {item}")
    return "\n".join(lines)


def build_sections_guide(template: Dict[str, Any]) -> str:
    """섹션 정의로부터 보고서 구조 가이드 텍스트를 생성합니다."""
    sections = template.get("sections", [])
    if not sections:
        return ""
    lines = ["[보고서 구조]"]
    for sec in sections:
        required_mark = "(필수)" if sec.get("required", True) else "(선택)"
        lines.append(f"### {sec['title']} {required_mark}")
        lines.append(f"  {sec.get('description', '')}")
    return "\n".join(lines)


def build_format_guide(template: Dict[str, Any]) -> str:
    """형식 가이드 텍스트를 생성합니다."""
    fmt = template.get("format", {})
    if not fmt:
        return ""
    hints = []
    if fmt.get("use_tables"):
        hints.append("정량 데이터는 마크다운 테이블로 정리하세요.")
    if fmt.get("use_code_blocks"):
        hints.append("코드나 명령어는 코드블록(```)으로 감싸세요.")
    if not fmt.get("use_code_blocks"):
        hints.append("코드블록 사용을 최소화하고 서술형으로 작성하세요.")
    if fmt.get("use_bullet_points"):
        hints.append("목록은 불릿(-)을 사용하여 간결하게 나열하세요.")
    if fmt.get("number_format"):
        hints.append(f"수치 표기: {fmt['number_format']}")
    if fmt.get("date_format"):
        hints.append(f"날짜 형식: {fmt['date_format']}")
    if not hints:
        return ""
    return "[형식 가이드]\n" + "\n".join(f"- {h}" for h in hints)


def build_full_department_context(department_id: str) -> Optional[str]:
    """department_id → 프롬프트에 삽입 가능한 전체 학과 컨텍스트 문자열 반환.

    Returns:
        학과 컨텍스트 문자열 또는 None (department_id가 없을 때)
    """
    if not department_id:
        return None
    try:
        template = load_department_template(department_id)
    except FileNotFoundError:
        return None

    parts = [
        build_department_system_prompt(template),
        "",
        build_sections_guide(template),
        "",
        build_format_guide(template),
    ]
    return "\n".join(p for p in parts if p.strip())
