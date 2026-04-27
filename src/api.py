"""FastAPI 보고서 생성 API"""

import json
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .department_loader import list_departments
from .document_generator import DocumentGenerator
from .report import (
    generate_report_stream,
    last_n_days,
    regenerate_for_department_stream,
    this_week,
)

app = FastAPI(title="Paran Auto Report API", version="1.0.0")


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

    generator = generate_report_stream(
        topic=req.topic,
        k=req.k,
        since=since,
        until=until,
        session_id=session_id,
        user_id=req.user_id,
    )
    return StreamingResponse(_sse(generator), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/report/regenerate")
def regenerate_report_endpoint(req: RegenerateRequest):
    """
    원본 보고서를 학과 맞춤으로 재생성 (SSE 스트리밍).

    Response: text/event-stream
      data: {"chunk": "..."}
      data: [DONE]
    """
    session_id = req.session_id or f"api-regen-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    try:
        generator = regenerate_for_department_stream(
            original_report=req.original_report,
            department_id=req.department_id,
            report_date=req.report_date,
            session_id=session_id,
            user_id=req.user_id,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    return StreamingResponse(_sse(generator), media_type="text/event-stream", headers=_SSE_HEADERS)


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
