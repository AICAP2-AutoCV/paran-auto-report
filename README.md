# 파RAG학기 — Notion 기반 자동 보고서 생성기

Notion 워크스페이스의 회의록과 계획서를 **RRF 하이브리드 검색(벡터 + BM25) + RAG**로 분석해 보고서를 자동 생성하는 서비스입니다.

> 시스템 내부 동작 방식과 핵심 알고리즘 6가지는 [ARCHITECTURE.md](./ARCHITECTURE.md)를 참고하세요.

---

## 두 폴더의 역할

이 프로젝트는 **두 폴더**로 나뉩니다. 둘 다 있어야 정상 동작합니다.

| 폴더 | 역할 | 기술 스택 |
|------|------|-----------|
| `paran-auto-report/` | **백엔드 API 서버** — Notion 데이터 수집, Vector DB 구축, 보고서 생성, Word/PDF 변환을 모두 담당합니다. FE 파일도 이 서버가 대신 서빙합니다. | Python, FastAPI, LangChain, Qdrant |
| `paran-auto-report-fe/` | **프론트엔드 UI** — 브라우저에서 보이는 채팅 화면입니다. 별도 서버 없이 API 서버(`/ui` 경로)가 파일을 그대로 제공합니다. | HTML, CSS, Vanilla JS |

> `paran-auto-report-fe/`는 **직접 실행할 필요가 없습니다.**  
> API 서버를 켜면 `http://localhost:8000/ui` 에서 FE가 자동으로 열립니다.

---

## 목차

1. [구동 순서 요약](#구동-순서-요약)
2. [전체 흐름](#전체-흐름)
3. [사전 준비](#사전-준비)
4. [설치](#설치)
5. [환경 변수 설정 (.env)](#환경-변수-설정-env)
6. [Vector DB 구축 (최초 1회)](#vector-db-구축-최초-1회)
7. [API 서버 실행](#api-서버-실행)
8. [FE(프론트엔드) 접속](#fe프론트엔드-접속)
9. [자주 쓰는 명령어 모음](#자주-쓰는-명령어-모음)
10. [테스트](#테스트)
11. [폴더 구조](#폴더-구조)

---

## 구동 순서 요약

처음 시작할 때는 아래 순서를 지켜야 합니다.

```
① .env 파일 작성          (최초 1회)
        ↓
② pip install             (최초 1회)
        ↓
③ Vector DB 구축           (최초 1회, Notion 데이터 변경 시 재실행)
   python scripts/build_vectordb.py build
        ↓
④ API 서버 실행            (사용할 때마다)
   python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload
        ↓
⑤ 브라우저에서 접속        (서버가 켜진 상태에서)
   http://localhost:8000/ui
```

**두 번째 실행부터는 ④ → ⑤ 만 하면 됩니다.**

---

## 전체 흐름

```
paran-auto-report/              paran-auto-report-fe/
(백엔드 API 서버)                (프론트엔드 — 서버가 /ui로 서빙)
       │                                  │
       │  ① Notion 데이터 수집             │
       │     build_vectordb.py            │
       │          ↓                       │
       │  Qdrant Vector DB (로컬)          │
       │          ↓                       │
       │  ② uvicorn 서버 실행              │
       │     :8000/api/*  (API)           │
       │     :8000/ui     (FE 서빙) ───────┘
       │          ↓
       └─── 브라우저: http://localhost:8000/ui
```

보고서 생성 요청이 들어오면 **RRF 하이브리드 검색**(벡터 + BM25 재순위)으로 관련 청크를 찾고, LLM이 스트리밍으로 보고서를 작성합니다.

---

## 사전 준비

| 항목 | 버전 | 설치 방법 |
|------|------|-----------|
| Python | 3.10 이상 | https://www.python.org/downloads/ |
| pip | 최신 | Python 설치 시 포함 |
| pandoc | 아무 버전 | https://pandoc.org/installing.html (PDF·Word 미리보기에 필요) |

> **pandoc 설치 확인:** 터미널에서 `pandoc --version` 을 입력했을 때 버전이 나오면 됩니다.

---

## 설치

```bash
# 1. 저장소 클론 (두 폴더가 나란히 있어야 FE 서빙이 동작합니다)
git clone https://github.com/AICAP2-AutoCV/paran-auto-report.git
git clone https://github.com/AICAP2-AutoCV/paran-auto-report-fe.git

# 2. 패키지 설치 (uv 권장)
cd paran-auto-report
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt

# pip를 사용하는 경우
# pip install -r requirements.txt
```

---

## 환경 변수 설정 (.env)

프로젝트 루트(`paran-auto-report/`)에 `.env` 파일을 만들어야 합니다.  
`.env.example`을 복사해 시작하세요.

```bash
cp .env.example .env
```

아래 내용을 참고해 **필수** 항목을 채워주세요.

```dotenv
# ─── 필수 ─────────────────────────────────────────────────────────────────────

# OpenRouter API 키 (https://openrouter.ai → API Keys)
# OpenAI API 키도 동일하게 사용 가능 (https://platform.openai.com/api-keys)
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Notion 통합 토큰 (https://www.notion.so/my-integrations → 새 통합 만들기)
# 권한: 콘텐츠 읽기 체크, 통합을 사용할 페이지/DB에 연결 필요
NOTION_TOKEN=ntn_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# 계획서·신청서가 있는 Notion 페이지 ID (페이지 URL 끝 32자리)
# 예: https://notion.so/팀이름/358a296c88538079888cc7d86c409498
#                                  ↑ 이 부분 (하이픈 포함)
NOTION_SOURCE_PAGES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 회의록이 있는 Notion 데이터베이스 ID (DB URL 끝 32자리)
NOTION_SOURCE_DATABASES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# ─── LLM / 임베딩 설정 ────────────────────────────────────────────────────────

# OpenRouter를 사용하는 경우 아래 두 줄 그대로 사용
OPENAI_API_KEY=${OPENROUTER_API_KEY}
OPENAI_BASE_URL=https://openrouter.ai/api/v1

# OpenAI 직접 사용하는 경우
# OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxx
# OPENAI_BASE_URL=https://api.openai.com/v1

# 보고서 생성에 사용할 LLM 모델
LLM_MODEL=openai/gpt-4o-mini

# 임베딩 모델 (변경 시 Vector DB를 다시 구축해야 합니다)
EMBEDDING_MODEL=openai/text-embedding-3-large

# ─── 선택 (바꾸지 않아도 동작합니다) ─────────────────────────────────────────

# Qdrant Vector DB 저장 경로 (기본: ./qdrant_data)
# QDRANT_PATH=./qdrant_data

# Langfuse 관측 도구 (없으면 비활성화됩니다)
# LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxx
# LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxx
# LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

### Notion 토큰·ID 얻는 방법

**Notion 통합 토큰 (`NOTION_TOKEN`)**

1. https://www.notion.so/my-integrations 접속
2. **"+ 새 통합"** 클릭
3. 이름 입력 → 워크스페이스 선택 → **"저장"**
4. 표시되는 **"내부 통합 토큰"** 복사 → `NOTION_TOKEN`에 붙여넣기
5. Notion에서 사용할 페이지/데이터베이스를 열고 우측 상단 **"···"** → **"연결"** → 방금 만든 통합 선택

**페이지/DB ID**

- 브라우저 주소창 URL에서 마지막 32자리 (하이픈 포함 36자리)가 ID입니다
- 예: `https://www.notion.so/myworkspace/358a296c-8853-8079-888c-c7d86c409498`
  - 페이지 ID: `358a296c-8853-8079-888c-c7d86c409498`

---

## Vector DB 구축 (최초 1회)

Notion 데이터를 긁어와 로컬 Vector DB에 저장합니다.  
**API 서버를 켜기 전에 반드시 먼저 실행해야 합니다.**

```bash
# paran-auto-report/ 폴더 안에서 실행
python scripts/build_vectordb.py build
```

Notion 페이지·DB 크기에 따라 수 분이 걸릴 수 있습니다.  
완료되면 `qdrant_data/` 폴더가 생성됩니다.

```bash
# Notion 데이터가 업데이트됐을 때 다시 구축
python scripts/build_vectordb.py build --force
```

---

## API 서버 실행

```bash
# paran-auto-report/ 폴더 안에서 실행
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload
```

아래 메시지가 보이면 성공입니다.

```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

> `--reload` 옵션은 코드를 수정하면 서버가 자동으로 재시작됩니다. 개발 중에 편리합니다.  
> 운영 환경에서는 `--reload` 를 제거하세요.

---

## FE(프론트엔드) 접속

API 서버가 실행 중인 상태에서 브라우저를 열고 아래 주소로 접속합니다.

```
http://localhost:8000/ui
```

> **별도 웹서버 불필요** — FE 파일(`paran-auto-report-fe/`)은 API 서버가 `/ui` 경로로 자동 서빙합니다.

### 첫 접속 시

1. 우측 상단 프로필 버튼(사람 아이콘)을 클릭해 **팀명 / 학과 / 학번 / 성명 / 역할** 을 입력하세요.
2. 입력한 정보는 브라우저에 저장되며 다운로드하는 보고서 표지에 자동으로 반영됩니다.
3. 채팅창에 보고서 주제를 입력하거나 상단 추천 칩을 클릭하면 생성이 시작됩니다.

---

## 자주 쓰는 명령어 모음

```bash
# Vector DB 검색 테스트 (내용이 잘 들어갔는지 확인)
python scripts/build_vectordb.py search "지난 주 개발 내용"

# API 서버 상태 확인
curl http://localhost:8000/health
# → {"status":"ok"} 가 나오면 정상

# 패키지 한번에 설치
pip install -r requirements.txt
```

---

## 테스트

```bash
# paran-auto-report/ 폴더 안에서 실행
pytest tests/ -v
```

`tests/test_tc14_stability.py` — 잘못된 입력(빈 주제, 잘못된 날짜, 없는 학과 ID, Qdrant 미연결 등) 상황에서 서버 무중단 여부를 검증합니다. LLM 실제 호출 없이 실행되므로 API 비용이 거의 없습니다.

---

## 폴더 구조

```
paran-auto-report/          ← API 서버 (이 폴더)
├── .env                    ← 환경 변수 (직접 생성, .env.example 참고)
├── .env.example            ← 환경 변수 템플릿
├── requirements.txt
├── ARCHITECTURE.md         ← 시스템 동작 방식 및 핵심 알고리즘 상세 설명
├── scripts/
│   ├── build_vectordb.py   ← Vector DB 구축 스크립트
│   ├── generate_report.py  ← CLI로 보고서 생성
│   └── setup-shell.sh      ← 쉘 환경 설정
├── src/
│   ├── api.py              ← FastAPI 앱 진입점
│   ├── config.py           ← 환경 변수 로드
│   ├── ingestion/          ← Notion 수집 · 멀티모달 임베딩
│   ├── report/             ← RRF 하이브리드 검색 + 보고서 생성
│   └── document/           ← Word / PDF 변환
├── config/departments/     ← 학과별 맞춤 재생성 YAML 설정
├── prompts/                ← 프롬프트 템플릿
├── tests/                  ← 안정성 테스트 (pytest)
├── experiments/            ← Retriever · Embedding · LLM 평가 스크립트 및 결과
├── qdrant_data/            ← Vector DB 데이터 (자동 생성, gitignore)
└── data/                   ← 수집된 Notion 원문 캐시 (gitignore)

paran-auto-report-fe/       ← 프론트엔드 (API 서버가 /ui로 서빙)
├── index.html
├── style.css
└── js/
    ├── state.js            ← API_BASE 등 전역 상태
    ├── api.js              ← fetch 래퍼
    ├── app.js              ← 메인 로직
    ├── ui.js               ← 렌더링 · 동의 확인 모달
    └── profile.js          ← 사용자 정보 관리
```
