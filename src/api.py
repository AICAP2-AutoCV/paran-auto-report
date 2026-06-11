"""FastAPI 보고서 생성 API"""

import json
import os
import tempfile
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .report.department import list_departments, load_department_template
from .document import DocumentGenerator
from .langfuse_feedback import save_feedback
from .report import (
    generate_report_stream,
    generate_report_with_images,
    last_n_days,
    regenerate_for_department_stream,
    this_week,
    add_glossary_to_report,
)

app = FastAPI(title="Paran Auto Report API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Trace-ID"],
)

_FE_DIR = Path(__file__).parent.parent.parent / "paran-auto-report-fe"
if _FE_DIR.exists():
    app.mount("/ui", StaticFiles(directory=str(_FE_DIR), html=True), name="frontend")


# ── 요청 모델 ────────────────────────────────────────────────────────────────

class GenerateRequest(BaseModel):
    topic: str
    k: int = 10
    since: Optional[str] = None       # YYYY-MM-DD
    last_days: Optional[int] = None
    use_this_week: bool = False
    until: Optional[str] = None       # YYYY-MM-DD
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    include_glossary: bool = False
    role: Optional[str] = None        # 사용자 역할 (예: "백엔드 개발", "ML 모델링")
    team_name: Optional[str] = None   # 팀명
    student_id: Optional[str] = None  # 학번
    department: Optional[str] = None  # 학과
    name: Optional[str] = None        # 성명


class GlossaryRequest(BaseModel):
    markdown: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class RegenerateRequest(BaseModel):
    original_report: str
    department_id: str
    report_date: Optional[str] = None
    session_id: Optional[str] = None
    user_id: Optional[str] = None


class ExportRequest(BaseModel):
    markdown: str
    format: str = "docx"              # "docx" | "pdf"
    title: str = "보고서"
    author: str = "Unknown"
    student_id: Optional[str] = None
    department: Optional[str] = None
    team_name: Optional[str] = None
    role: Optional[str] = None        # 사용자 역할
    images: List[Dict[str, Any]] = Field(default_factory=list)


class FeedbackRequest(BaseModel):
    trace_id: str
    score: int = Field(..., ge=0, le=10)
    comment: Optional[str] = None
    feedback_type: str = "user_satisfaction"


class FeedbackResponse(BaseModel):
    code: int = 1
    message: str = "피드백이 저장되었습니다."
    success: bool = True


# ── 내부 헬퍼 ────────────────────────────────────────────────────────────────

def _parse_since(req: GenerateRequest) -> Optional[datetime]:
    if req.since:
        return datetime.strptime(req.since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if req.last_days:
        return last_n_days(req.last_days)
    if req.use_this_week:
        return this_week()
    return None


def _parse_until(s: Optional[str]) -> Optional[datetime]:
    if s:
        return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return None


def _sse(generator):
    """동기 제너레이터를 SSE 포맷으로 변환."""
    for chunk in generator:
        payload = json.dumps({"chunk": chunk}, ensure_ascii=False)
        yield f"data: {payload}\n\n"
    yield "data: [DONE]\n\n"


def _stream_with_glossary(generator, session_id: Optional[str], user_id: Optional[str], trace_id: str):
    """보고서 스트림 완료 후 용어 해설 섹션을 추가로 yield."""
    accumulated: list[str] = []
    for chunk in generator:
        accumulated.append(chunk)
        yield chunk
    full_report = "".join(accumulated)
    enriched, _ = add_glossary_to_report(full_report, session_id=session_id, user_id=user_id, trace_id=trace_id)
    suffix = enriched[len(full_report):]
    if suffix:
        yield suffix


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


# ── 엔드포인트 ───────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/departments")
def get_departments():
    """사용 가능한 학과 목록 반환."""
    return list_departments()


@app.post("/report/generate")
def generate_report_endpoint(req: GenerateRequest):
    """
    Notion RAG 기반 보고서 생성 (SSE 스트리밍).

    Response: text/event-stream
      data: {"chunk": "..."}   — 생성 중인 텍스트 조각
      data: [DONE]             — 완료 신호
    """
    since = _parse_since(req)
    until = _parse_until(req.until)
    session_id = req.session_id or f"api-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trace_id = uuid.uuid4().hex

    generator = generate_report_stream(
        topic=req.topic,
        k=req.k,
        since=since,
        until=until,
        session_id=session_id,
        user_id=req.user_id,
        trace_id=trace_id,
        role=req.role,
        team_name=req.team_name,
        student_id=req.student_id,
        department=req.department,
        name=req.name,
    )
    if req.include_glossary:
        generator = _stream_with_glossary(generator, session_id=session_id, user_id=req.user_id, trace_id=trace_id)
    headers = {**_SSE_HEADERS, "X-Trace-ID": trace_id}
    return StreamingResponse(_sse(generator), media_type="text/event-stream", headers=headers)


@app.post("/report/generate-full")
def generate_report_full_endpoint(req: GenerateRequest):
    """Notion RAG 보고서 본문과 관련 이미지 메타데이터를 함께 반환."""
    since = _parse_since(req)
    until = _parse_until(req.until)
    session_id = req.session_id or f"api-full-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trace_id = uuid.uuid4().hex

    payload = generate_report_with_images(
        topic=req.topic,
        k=req.k,
        since=since,
        until=until,
        session_id=session_id,
        user_id=req.user_id,
        trace_id=trace_id,
        role=req.role,
        team_name=req.team_name,
        student_id=req.student_id,
        department=req.department,
        name=req.name,
    )
    if req.include_glossary:
        enriched, terms = add_glossary_to_report(
            payload["report"],
            session_id=session_id,
            user_id=req.user_id,
            trace_id=trace_id,
        )
        payload = {**payload, "report": enriched, "glossary_terms": terms}
    return {**payload, "trace_id": trace_id}


@app.post("/report/regenerate")
def regenerate_report_endpoint(req: RegenerateRequest):
    """
    원본 보고서를 학과 맞춤으로 재생성 (SSE 스트리밍).

    Response: text/event-stream
      data: {"chunk": "..."}
      data: [DONE]
    """
    session_id = req.session_id or f"api-regen-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trace_id = uuid.uuid4().hex

    # regenerate_for_department_stream 은 제너레이터 함수이므로 body가 lazy 실행됨.
    # FileNotFoundError 를 스트리밍 전에 잡으려면 템플릿을 여기서 미리 검증해야 함.
    try:
        load_department_template(req.department_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    generator = regenerate_for_department_stream(
        original_report=req.original_report,
        department_id=req.department_id,
        report_date=req.report_date,
        session_id=session_id,
        user_id=req.user_id,
        trace_id=trace_id,
    )

    headers = {**_SSE_HEADERS, "X-Trace-ID": trace_id}
    return StreamingResponse(_sse(generator), media_type="text/event-stream", headers=headers)


@app.post("/report/glossary")
def add_glossary_endpoint(req: GlossaryRequest):
    """기존 보고서 마크다운에 용어 강조 및 해설 섹션을 추가하여 반환.

    Request body:
      markdown   : 보고서 마크다운 텍스트
      session_id : (선택) Langfuse 세션 ID
      user_id    : (선택) 사용자 ID

    Response:
      markdown        : 용어 강조 + 해설 섹션이 추가된 마크다운
      glossary_terms  : 추출된 용어 목록 [{term, explanation}, ...]
    """
    session_id = req.session_id or f"api-glossary-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trace_id = uuid.uuid4().hex
    try:
        enriched, terms = add_glossary_to_report(
            req.markdown,
            session_id=session_id,
            user_id=req.user_id,
            trace_id=trace_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"용어 해설 생성 실패: {e}")
    return {"markdown": enriched, "glossary_terms": terms, "trace_id": trace_id}


@app.post("/document/export")
def export_document(req: ExportRequest):
    """
    마크다운 텍스트를 docx 또는 pdf 파일로 변환하여 다운로드.

    Request body:
      markdown   : 마크다운 텍스트
      format     : "docx" | "pdf"  (기본값: "docx")
      title      : 문서 제목
      author     : 작성자
    """
    if req.format not in ("docx", "pdf"):
        raise HTTPException(status_code=400, detail="format은 'docx' 또는 'pdf'만 지원합니다.")

    suffix = f".{req.format}"
    fd, tmp_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    try:
        gen = DocumentGenerator()
        gen.generate_from_markdown(
            markdown_text=req.markdown,
            output_path=tmp_path,
            title=req.title,
            author=req.author,
            student_id=req.student_id,
            department=req.department,
            team_name=req.team_name,
            role=req.role,
            images=req.images,
        )
    except Exception as e:
        os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"문서 생성 실패: {e}")

    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if req.format == "docx"
        else "application/pdf"
    )
    filename = f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}{suffix}"

    return FileResponse(
        path=tmp_path,
        media_type=media_type,
        filename=filename,
        background=BackgroundTask(os.unlink, tmp_path),
    )


@app.post("/document/preview")
def preview_document(req: ExportRequest):
    """
    마크다운을 Word로 변환 후 pandoc으로 HTML 변환하여 미리보기용 HTML 반환.
    """
    import subprocess
    from fastapi.responses import HTMLResponse

    fd, tmp_docx = tempfile.mkstemp(suffix=".docx")
    os.close(fd)
    try:
        gen = DocumentGenerator()
        gen.generate_from_markdown(
            markdown_text=req.markdown,
            output_path=tmp_docx,
            title=req.title,
            author=req.author,
            student_id=req.student_id,
            department=req.department,
            team_name=req.team_name,
            role=req.role,
            images=req.images,
        )
        result = subprocess.run(
            ["pandoc", tmp_docx, "-f", "docx", "-t", "html5", "--standalone",
             "--metadata", "charset=utf-8"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"HTML 변환 실패: {result.stderr[:200]}")
        html = result.stdout
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"미리보기 생성 실패: {e}")
    finally:
        if os.path.exists(tmp_docx):
            os.unlink(tmp_docx)

    return HTMLResponse(content=html)


@app.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest):
    """생성된 보고서에 대한 사용자 피드백을 Langfuse score로 저장."""
    try:
        success = save_feedback(
            trace_id=req.trace_id,
            score=req.score,
            comment=req.comment,
            feedback_type=req.feedback_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not success:
        raise HTTPException(status_code=500, detail="피드백 저장에 실패했습니다.")

    return FeedbackResponse()
