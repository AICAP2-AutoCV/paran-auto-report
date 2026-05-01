"""학과별 보고서 템플릿 로더"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "config" / "departments"


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def list_departments() -> List[Dict[str, str]]:
    departments = []
    for yaml_file in sorted(TEMPLATES_DIR.rglob("*.yaml")):
        try:
            data = _load_yaml(yaml_file)
            dept = data.get("department", {})
            departments.append({
                "id": dept.get("id", yaml_file.stem),
                "name": dept.get("name", ""),
                "name_en": dept.get("name_en", ""),
                "description": dept.get("description", ""),
                "college": dept.get("college", yaml_file.parent.name),
            })
        except Exception:
            continue
    return departments


def load_department_template(department_id: str) -> Dict[str, Any]:
    yaml_path = TEMPLATES_DIR / f"{department_id}.yaml"
    if not yaml_path.exists():
        for f in TEMPLATES_DIR.rglob("*.yaml"):
            data = _load_yaml(f)
            if data.get("department", {}).get("id") == department_id:
                return data
        available = [f.stem for f in TEMPLATES_DIR.rglob("*.yaml")]
        raise FileNotFoundError(
            f"학과 템플릿 '{department_id}'를 찾을 수 없습니다. "
            f"사용 가능한 템플릿: {available}"
        )
    return _load_yaml(yaml_path)


def build_department_system_prompt(template: Dict[str, Any]) -> str:
    dept = template.get("department", {})
    tone = template.get("tone", {})
    purpose = template.get("purpose", "")
    translation_guides = template.get("translation_guides", [])
    terminology = template.get("terminology", [])
    analogy_examples = template.get("analogy_examples", [])
    avoid = template.get("avoid", [])

    lines = [
        f"당신은 **{dept.get('name', '')}** 학생을 위한 보고서 재설명 전문가입니다.",
        "",
        "[학과 특성]",
        dept.get("description", ""),
        "",
        "[재작성 목적]",
        purpose,
        "",
        "[작성 톤 & 스타일]",
        tone.get("description", ""),
        "",
        "[학과별 이해 가이드]",
    ]
    for item in translation_guides:
        lines.append(f"- {item}")
    if terminology:
        lines.extend(["", "[활용하면 좋은 학과 용어]"])
        lines.append(", ".join(str(item) for item in terminology))
    if analogy_examples:
        lines.extend(["", "[설명용 비유 예시]"])
        for item in analogy_examples:
            lines.append(f"- {item}")
    if avoid:
        lines.extend(["", "[주의할 점]"])
        for item in avoid:
            lines.append(f"- {item}")
    return "\n".join(lines)


def build_sections_guide(template: Dict[str, Any]) -> str:
    sections = template.get("sections", [])
    if not sections:
        return ""
    lines = ["[권장 재구성 흐름]"]
    for sec in sections:
        required_mark = "(필수)" if sec.get("required", True) else "(선택)"
        lines.append(f"### {sec['title']} {required_mark}")
        lines.append(f"  {sec.get('description', '')}")
    return "\n".join(lines)


def build_format_guide(template: Dict[str, Any]) -> str:
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
