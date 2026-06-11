"""
TC-14 안정성 테스트

API에 잘못된 입력(빈 주제 · 잘못된 날짜 · 없는 학과 ID · Qdrant 미연결 등)을 넣어
- 적절한 오류 메시지가 반환되는지
- 서버가 무중단 상태를 유지하는지
를 검증합니다.

대부분의 오류 경로는 LLM 호출 전에 막히거나 mock 처리되므로 API 비용이 거의 없습니다.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from src.api import app


@pytest.fixture(scope="module")
def client():
    # raise_server_exceptions=False: 핸들러 내 미처리 예외를 HTTP 500으로 변환
    # (서버가 죽지 않고 살아있음을 TestClient 레벨에서 시뮬레이션)
    return TestClient(app, raise_server_exceptions=False)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────

def assert_server_alive(client):
    """테스트 후 /health 로 서버 무중단 확인."""
    resp = client.get("/health")
    assert resp.status_code == 200, "/health 가 200이 아님 — 서버가 다운됐을 가능성"
    assert resp.json() == {"status": "ok"}


# ── 1. Pydantic 필드 검증 ────────────────────────────────────────────────────

class TestPydanticValidation:
    """Pydantic 스키마 수준에서 걸러지는 입력 (LLM 호출 없음)."""

    def test_missing_topic_returns_422(self, client):
        """필수 필드 topic 누락 → 422 Unprocessable Entity."""
        resp = client.post("/report/generate", json={})
        assert resp.status_code == 422

    def test_missing_regenerate_fields_returns_422(self, client):
        """original_report · department_id 모두 누락 → 422."""
        resp = client.post("/report/regenerate", json={})
        assert resp.status_code == 422

    def test_invalid_export_format_returns_400(self, client):
        """지원하지 않는 export 형식(xlsx) → 400."""
        resp = client.post("/document/export", json={
            "markdown": "# 테스트 보고서",
            "format": "xlsx",
        })
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "docx" in detail or "pdf" in detail

    def test_feedback_score_out_of_range_returns_422(self, client):
        """feedback score 범위 초과(0~10) → 422."""
        resp = client.post("/feedback", json={
            "trace_id": "abc123",
            "score": 99,
        })
        assert resp.status_code == 422


# ── 2. 날짜 형식 오류 ────────────────────────────────────────────────────────

class TestInvalidDateFormat:
    """잘못된 날짜 문자열 — Pydantic은 통과하지만 strptime에서 ValueError."""

    def test_since_wrong_format_returns_error(self, client):
        """since='not-a-date' → ValueError → HTTP 에러, 서버 무중단."""
        resp = client.post("/report/generate", json={
            "topic": "머신러닝",
            "since": "not-a-date",
        })
        assert resp.status_code in (400, 422, 500)
        assert_server_alive(client)

    def test_since_no_hyphen_returns_error(self, client):
        """since='20260601' (하이픈 없는 형식) → 파싱 실패 → 에러 반환."""
        resp = client.post("/report/generate", json={
            "topic": "데이터 분석",
            "since": "20260601",
        })
        assert resp.status_code in (400, 422, 500)
        assert_server_alive(client)

    def test_until_wrong_format_returns_error(self, client):
        """until='2026/12/31' → 파싱 실패 → 에러 반환."""
        resp = client.post("/report/generate", json={
            "topic": "프로젝트 회의",
            "until": "2026/12/31",
        })
        assert resp.status_code in (400, 422, 500)
        assert_server_alive(client)

    def test_since_impossible_date_returns_error(self, client):
        """since='2026-99-99' (불가능한 날짜) → 에러 반환."""
        resp = client.post("/report/generate", json={
            "topic": "테스트",
            "since": "2026-99-99",
        })
        assert resp.status_code in (400, 422, 500)
        assert_server_alive(client)


# ── 3. 학과 ID 오류 ──────────────────────────────────────────────────────────

class TestDepartmentErrors:
    """존재하지 않는 학과 ID — LLM 호출 전에 FileNotFoundError 발생."""

    def test_nonexistent_department_id_returns_404(self, client):
        """없는 department_id → FileNotFoundError → 404."""
        resp = client.post("/report/regenerate", json={
            "original_report": "# 테스트 보고서\n활동 내용입니다.",
            "department_id": "this_dept_absolutely_does_not_exist_xyz123",
        })
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        # 오류 메시지에 실패 이유 포함 확인
        assert "찾을 수 없" in detail or "department" in detail.lower()

    def test_empty_department_id_returns_404(self, client):
        """빈 문자열 department_id → 404."""
        resp = client.post("/report/regenerate", json={
            "original_report": "# 보고서",
            "department_id": "",
        })
        assert resp.status_code == 404

    def test_department_id_with_path_traversal_returns_404(self, client):
        """경로 순회 시도 department_id → 404 (yaml 없음)."""
        resp = client.post("/report/regenerate", json={
            "original_report": "# 보고서",
            "department_id": "../../etc/passwd",
        })
        assert resp.status_code == 404

    def test_get_departments_list_succeeds(self, client):
        """/departments 는 항상 목록 반환 (에러 없음)."""
        resp = client.get("/departments")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ── 4. Qdrant 연결 실패 ───────────────────────────────────────────────────────

class TestQdrantFailure:
    """Qdrant 연결/컬렉션 오류 — generate-full 엔드포인트로 테스트
    (스트리밍이 아닌 일반 응답이므로 예외가 온전히 500으로 전환됨)."""

    def test_connection_error_returns_500(self, client):
        """Qdrant 서버 연결 실패(ConnectionError) → 500, 서버 무중단."""
        with patch(
            "src.report.generator.load_vectorstore",
            side_effect=ConnectionError("Qdrant 서버에 연결할 수 없습니다"),
        ):
            resp = client.post("/report/generate-full", json={"topic": "머신러닝 모델 학습"})
        assert resp.status_code == 500
        assert_server_alive(client)

    def test_collection_not_found_returns_500(self, client):
        """Qdrant 컬렉션 없음(RuntimeError) → 500, 서버 무중단."""
        with patch(
            "src.report.generator.load_vectorstore",
            side_effect=RuntimeError("Collection 'notion_docs' doesn't exist"),
        ):
            resp = client.post("/report/generate-full", json={"topic": "데이터 파이프라인"})
        assert resp.status_code == 500
        assert_server_alive(client)

    def test_timeout_returns_500(self, client):
        """Qdrant 응답 타임아웃(TimeoutError) → 500, 서버 무중단."""
        with patch(
            "src.report.generator.load_vectorstore",
            side_effect=TimeoutError("Qdrant 응답 시간 초과"),
        ):
            resp = client.post("/report/generate-full", json={"topic": "프로젝트 회의록"})
        assert resp.status_code == 500
        assert_server_alive(client)

    def test_qdrant_streaming_error_server_survives(self, client):
        """/report/generate (스트리밍) Qdrant 실패 → 서버는 살아있어야 함."""
        with patch(
            "src.report.generator.load_vectorstore",
            side_effect=ConnectionError("Qdrant 연결 끊김"),
        ):
            # 스트리밍 응답은 헤더 전송 타이밍에 따라 200 또는 500
            resp = client.post("/report/generate", json={"topic": "테스트 주제"})
        assert resp.status_code in (200, 500)
        # 서버 생존 확인이 핵심
        assert_server_alive(client)


# ── 5. 빈 주제 경계값 ────────────────────────────────────────────────────────

class TestEmptyTopic:
    """빈 주제 — 현재 API 레이어에서 막지 않음, Qdrant mock 필요."""

    def test_empty_topic_does_not_crash_server(self, client):
        """빈 주제로 요청해도 서버가 살아있어야 함."""
        mock_vs = MagicMock()
        mock_vs.similarity_search.return_value = []

        with patch("src.report.generator.load_vectorstore", return_value=mock_vs):
            mock_chain = MagicMock()
            mock_chain.__or__ = MagicMock(return_value=mock_chain)
            mock_chain.invoke.return_value = "빈 주제 테스트 응답"
            with patch("src.report.generator.ChatOpenAI", return_value=mock_chain):
                resp = client.post("/report/generate-full", json={"topic": ""})

        # 어떤 응답이든 서버가 살아있어야 함
        assert resp.status_code in (200, 400, 422, 500)
        assert_server_alive(client)


# ── 6. 연속 오류 후 서버 생존 확인 ───────────────────────────────────────────

class TestServerSurvival:
    """에러 폭탄 이후에도 /health 는 반드시 200."""

    def test_health_always_returns_ok(self, client):
        """/health 는 어떤 상황에서도 200 {status: ok} 반환."""
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_server_survives_all_error_types_sequentially(self, client):
        """422 → 날짜오류(500) → 없는학과(404) → 잘못된포맷(400) → Qdrant실패(500)
        순서로 연속 오류를 발생시킨 뒤 /health 정상 확인."""

        # 1) 필수 필드 누락 → 422
        r1 = client.post("/report/generate", json={})
        assert r1.status_code == 422

        # 2) 잘못된 날짜 → 500
        r2 = client.post("/report/generate", json={"topic": "x", "since": "bad-date"})
        assert r2.status_code in (400, 422, 500)

        # 3) 없는 학과 → 404
        r3 = client.post("/report/regenerate", json={
            "original_report": "내용",
            "department_id": "nonexistent_dept_xyz_999",
        })
        assert r3.status_code == 404

        # 4) 지원하지 않는 export 포맷 → 400
        r4 = client.post("/document/export", json={
            "markdown": "# 보고서",
            "format": "ppt",
        })
        assert r4.status_code == 400

        # 5) Qdrant 연결 실패 → 500
        with patch(
            "src.report.generator.load_vectorstore",
            side_effect=ConnectionError("연결 실패"),
        ):
            r5 = client.post("/report/generate-full", json={"topic": "테스트"})
        assert r5.status_code == 500

        # 서버 생존 확인
        assert_server_alive(client)
