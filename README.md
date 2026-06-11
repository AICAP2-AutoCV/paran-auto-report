# 파RAG학기: Notion 기반 자동 보고서 생성기

Notion의 회의록과 계획서를 검색해 주간 활동 보고서를 자동으로 생성하는 RAG 서비스입니다.
벡터 검색과 BM25를 결합한 RRF 하이브리드 검색을 사용하며, 생성한 보고서는 Word 또는 PDF로 내보낼 수 있습니다.

## 주요 기능

- Notion 페이지와 데이터베이스 수집
- Qdrant 기반 로컬 Vector DB 구축
- Dense + BM25 기반 RRF 하이브리드 검색
- 보고서 실시간 생성 및 관련 이미지 반영
- 학과별 양식에 맞춘 보고서 재생성
- 전문 용어 해설 추가
- Word(`.docx`) 및 PDF 내보내기
- Langfuse 기반 생성 추적 및 사용자 피드백 수집

## 목차

- [프로젝트 구성](#프로젝트-구성)
- [빠른 시작](#빠른-시작)
- [사용 방법](#사용-방법)
- [동작 구조](#동작-구조)
- [주요 API](#주요-api)
- [프로젝트 구조](#프로젝트-구조)
- [RAG 평가 결과](#rag-평가-결과)
- [문제 해결](#문제-해결)

## 프로젝트 구성

서비스는 백엔드와 프론트엔드 두 저장소로 구성됩니다. 두 폴더를 같은 상위 디렉터리에 배치해야 합니다.

| 저장소 | 역할 | 기술 스택 |
|---|---|---|
| [`paran-auto-report`](https://github.com/AICAP2-AutoCV/paran-auto-report) | Notion 수집, 검색, 보고서 생성, 문서 변환, API 및 프론트엔드 서빙 | Python, FastAPI, LangChain, Qdrant |
| [`paran-auto-report-fe`](https://github.com/AICAP2-AutoCV/paran-auto-report-fe) | 채팅형 보고서 생성 UI | HTML, CSS, Vanilla JavaScript |

프론트엔드는 별도 서버를 실행하지 않습니다. 백엔드가 인접한 `paran-auto-report-fe/` 폴더를 감지해 `/ui` 경로로 제공합니다.

```text
AutoCV/
├── paran-auto-report/
└── paran-auto-report-fe/
```

## 빠른 시작

### 1. 사전 준비

| 항목 | 요구 사항 | 용도 |
|---|---|---|
| Python | 3.10 이상 | API 및 RAG 파이프라인 실행 |
| `uv` 또는 `pip` | 최신 버전 권장 | Python 패키지 설치 |
| pandoc | 선택 | 실제 Word 파일 미리보기 및 PDF 변환 |
| OpenAI 또는 OpenRouter API 키 | 필수 | LLM 및 임베딩 호출 |
| Notion 통합 토큰 | 필수 | 페이지와 데이터베이스 읽기 |

pandoc 설치 여부는 다음 명령으로 확인할 수 있습니다.

```bash
pandoc --version
```

### 2. 저장소 설치

```bash
git clone https://github.com/AICAP2-AutoCV/paran-auto-report.git
git clone https://github.com/AICAP2-AutoCV/paran-auto-report-fe.git

cd paran-auto-report
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

`pip`를 사용하는 경우:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows에서는 가상 환경을 다음과 같이 활성화합니다.

```powershell
.venv\Scripts\activate
```

### 3. 환경 변수 설정

예제 파일을 복사한 뒤 필요한 값을 입력합니다.

```bash
cp .env.example .env
```

최소 설정 예시:

```dotenv
# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-xxxxxxxx
OPENAI_API_KEY=${OPENROUTER_API_KEY}
OPENAI_BASE_URL=https://openrouter.ai/api/v1

# Notion
NOTION_TOKEN=ntn_xxxxxxxxxxxxxxxx
NOTION_SOURCE_PAGES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
NOTION_SOURCE_DATABASES=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 모델
LLM_MODEL=openai/gpt-4o-mini
EMBEDDING_MODEL=openai/text-embedding-3-large
```

OpenAI API를 직접 사용하는 경우:

```dotenv
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-large
```

여러 Notion 페이지나 데이터베이스를 연결하려면 ID를 쉼표로 구분합니다.

```dotenv
NOTION_SOURCE_PAGES=page-id-1,page-id-2
NOTION_SOURCE_DATABASES=database-id-1,database-id-2
```

선택 환경 변수:

| 변수 | 기본값 | 설명 |
|---|---|---|
| `QDRANT_PATH` | `./qdrant_data` | 로컬 Vector DB 저장 경로 |
| `QDRANT_COLLECTION` | `notion_docs` | Qdrant 컬렉션 이름 |
| `VISION_MODEL` | `openai/gpt-4o-mini` | 이미지 설명 모델 |
| `LANGFUSE_SECRET_KEY` | 없음 | Langfuse 비밀 키 |
| `LANGFUSE_PUBLIC_KEY` | 없음 | Langfuse 공개 키 |
| `LANGFUSE_BASE_URL` | `https://cloud.langfuse.com` | Langfuse 서버 주소 |

> 임베딩 모델을 변경하면 기존 벡터와 호환되지 않으므로 Vector DB를 다시 구축해야 합니다.

### 4. Notion 연결

1. [Notion 통합 관리](https://www.notion.so/my-integrations)에서 새 통합을 만듭니다.
2. 통합에 콘텐츠 읽기 권한을 부여하고 내부 통합 토큰을 `NOTION_TOKEN`에 입력합니다.
3. 수집할 페이지와 데이터베이스에서 `연결` 메뉴를 열어 생성한 통합을 추가합니다.
4. Notion URL의 마지막 32자리 ID를 `.env`에 입력합니다. 하이픈이 포함된 36자리 형식도 사용할 수 있습니다.

```text
https://www.notion.so/workspace/358a296c-8853-8079-888c-c7d86c409498
                                └──────────── 페이지 또는 DB ID ────────────┘
```

### 5. Vector DB 구축

API를 처음 실행하기 전에 Notion 데이터를 수집하고 임베딩합니다.

```bash
python scripts/build_vectordb.py build
```

기존 컬렉션을 삭제하고 전체 데이터를 다시 구축하려면:

```bash
python scripts/build_vectordb.py build --force
```

수집량을 제한해 테스트하려면:

```bash
python scripts/build_vectordb.py build --limit 10
```

### 6. 서버 실행

```bash
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload
```

실행 후 아래 주소를 사용할 수 있습니다.

| 주소 | 설명 |
|---|---|
| <http://localhost:8000/ui> | 보고서 생성 화면 |
| <http://localhost:8000/docs> | Swagger API 문서 |
| <http://localhost:8000/health> | 서버 상태 확인 |

두 번째 실행부터는 Vector DB를 다시 만들 필요 없이 서버만 실행하면 됩니다. Notion 원본이 변경되었을 때만 DB를 재구축하세요.

## 사용 방법

### 웹 UI

1. <http://localhost:8000/ui>에 접속합니다.
2. 최초 접속 시 팀명, 학과, 학번, 성명, 역할을 입력합니다.
3. 채팅창에 원하는 보고서와 기간을 자연어로 입력합니다.
4. 생성된 보고서를 미리보거나 학과별 양식으로 재생성합니다.
5. AI 생성물 확인 절차를 거친 뒤 Word 또는 PDF로 다운로드합니다.

입력 예시:

```text
이번 주 활동 요약해줘
최근 14일 동안 진행한 개발 업무를 정리해줘
2026-06-01부터 2026-06-07까지의 회의 내용을 보고서로 작성해줘
```

프로필은 브라우저 `localStorage`에 저장되며 우측 상단 프로필 버튼에서 수정할 수 있습니다.

### CLI

웹 UI 없이 터미널에서도 보고서를 생성할 수 있습니다.

```bash
# 마크다운 생성
python scripts/generate_report.py "이번 주 개발 활동 요약"

# 최근 7일 자료로 Word 보고서 생성
python scripts/generate_report.py "주간 활동 보고서" \
  --last-days 7 \
  --format docx \
  --output output/weekly-report.docx

# 지정 기간의 보고서를 스트리밍으로 생성
python scripts/generate_report.py "프로젝트 진행 보고서" \
  --since 2026-06-01 \
  --until 2026-06-07 \
  --stream
```

지원 출력 형식은 `md`, `docx`, `pdf`, `all`입니다.

## 자주 쓰는 명령어

```bash
# Vector DB 검색 확인
python scripts/build_vectordb.py search "지난 주 개발 내용" --k 5

# 서버 상태 확인
curl http://localhost:8000/health

# API 서버 실행
python -m uvicorn src.api:app --host 127.0.0.1 --port 8000 --reload
```

## 동작 구조

```text
Notion 페이지 및 데이터베이스
          |
          v
텍스트 청킹 + 이미지 설명 생성
          |
          v
임베딩 및 Qdrant 저장
          |
          v
Dense 검색 + BM25 검색
          |
          v
RRF 재순위 + 날짜 필터
          |
          v
LLM 보고서 생성
          |
          v
웹 미리보기 / 학과별 재생성 / Word·PDF 내보내기
```

보고서 생성 시 관련 문서를 Dense 검색과 BM25로 각각 찾고, RRF로 결과를 결합합니다. 이후 계획서와 활동 기록을 프롬프트에 구성해 LLM이 보고서를 생성합니다.

## 주요 API

| Method | Endpoint | 설명 |
|---|---|---|
| `GET` | `/health` | 서버 상태 확인 |
| `GET` | `/departments` | 사용 가능한 학과 목록 조회 |
| `POST` | `/report/generate` | SSE 방식 보고서 생성 |
| `POST` | `/report/generate-full` | 보고서와 관련 이미지 메타데이터 생성 |
| `POST` | `/report/regenerate` | 학과별 양식으로 보고서 재생성 |
| `POST` | `/report/glossary` | 전문 용어 해설 추가 |
| `POST` | `/document/export` | Word 또는 PDF 내보내기 |
| `POST` | `/document/preview` | 실제 문서 기반 HTML 미리보기 |
| `POST` | `/feedback` | Langfuse 사용자 피드백 저장 |

요청 및 응답 스키마는 서버 실행 후 <http://localhost:8000/docs>에서 확인할 수 있습니다.

## 프로젝트 구조

```text
paran-auto-report/
├── .env.example                 # 환경 변수 예시
├── requirements.txt             # Python 의존성
├── scripts/
│   ├── build_vectordb.py        # Notion 수집 및 Vector DB 구축
│   ├── generate_report.py       # 보고서 생성 CLI
│   └── update_payload_dates.py  # 기존 payload 날짜 보정
├── src/
│   ├── api.py                   # FastAPI 진입점
│   ├── config.py                # 환경 변수 및 기본 설정
│   ├── ingestion/               # Notion 수집, 청킹, 임베딩, 이미지 처리
│   ├── report/                  # 검색, 보고서 생성, 학과별 재생성
│   └── document/                # Markdown, Word, PDF 변환
├── config/departments/          # 학과별 보고서 설정
├── prompts/                     # 보고서 재생성 프롬프트
├── experiments/                 # 검색 및 모델 평가 코드
├── data/                        # 수집 데이터와 이미지
└── qdrant_data/                 # 로컬 Qdrant 데이터
```

## RAG 평가 결과

실제 보고서를 기준으로 Retriever, Embedding, 생성 LLM을 단계별 평가했습니다. 평가는 내용 일치도, 문체 유사도, 정보 완성도를 각각 1~5점으로 측정했습니다.

### 최종 구성

| 구성 요소 | 선택 결과 | 선정 이유 |
|---|---|---|
| Retriever | RRF | BM25와 Dense 검색의 장점을 결합하고 문체 유사도에서 가장 높은 점수 기록 |
| Embedding | `openai/text-embedding-3-large` | 비교 모델 중 내용 일치도와 전체 평균이 가장 높음 |
| 생성 LLM | `gemini-3-flash` | 완성도, 생성 속도, 비용을 종합했을 때 가장 효율적 |

### Retriever 비교

| 순위 | Retriever | 내용 | 문체 | 완성 | 평균 |
|:---:|---|:---:|:---:|:---:|:---:|
| 1 | BM25 | 2.17 | 2.50 | 2.50 | **2.39** |
| 2 | **RRF** | 2.08 | **2.67** | 2.33 | **2.36** |
| 3 | Dense | 1.92 | 2.58 | 2.42 | **2.31** |
| 4 | RRF + MultiQuery | 1.92 | 2.50 | 2.17 | **2.20** |

BM25와 RRF의 평균 차이는 0.03점이었습니다. RRF는 문체 유사도가 가장 높고 BM25와 Dense 결과를 함께 활용하므로 최종 Retriever로 선택했습니다.

### Embedding 비교

| 순위 | Embedding | 내용 | 문체 | 완성 | 평균 |
|:---:|---|:---:|:---:|:---:|:---:|
| 1 | **OpenAI text-embedding-3-large** | **3.50** | **3.58** | 2.83 | **3.38** |
| 2 | Gemini embedding | 2.33 | 3.08 | 2.58 | **2.88** |
| 3 | Qwen3 embedding 8B | 2.00 | 2.92 | 2.50 | **2.73** |

### 생성 LLM 비교

| 순위 | LLM | 내용 | 문체 | 완성 | 평균 | Self-judge 가능성 |
|:---:|---|:---:|:---:|:---:|:---:|:---:|
| 1 | `claude-opus-4-7` | 4.42 | 4.08 | 2.92 | **3.81** | 있음 |
| 2 | **`gemini-3-flash`** | 4.00 | 3.50 | **3.17** | **3.65** | 없음 |
| 3 | `gpt-5-5` | 4.08 | 3.58 | 2.75 | **3.54** | 있음 |
| 4 | `gpt-5-mini` | 2.83 | 2.92 | 2.17 | **2.90** | 없음 |
| 5 | `hy3-preview` | 2.92 | 3.25 | 1.92 | **2.88** | 없음 |
| 6 | `deepseek-v4-flash` | 2.83 | 2.92 | 1.92 | **2.77** | 있음 |

Self-judge 가능성이 있는 상위 모델은 `owl-alpha`, `hy3-preview`, `minimax-m2.7`로 다시 평가했습니다. 순위는 `claude-opus-4-7` 1위, `gemini-3-flash` 2위, `gpt-5-5` 3위로 유지되었습니다.

| 모델 | 평균 생성 시간 | 중앙값 |
|---|---:|---:|
| **`gemini-3-flash`** | **8.2초** | 8.3초 |
| `claude-opus-4-7` | 36.5초 | 36.4초 |
| `gpt-5-5` | 42.3초 | 38.0초 |

`gemini-3-flash`는 전체 점수 2위이면서 정보 완성도와 속도에서 우위를 보여 최종 모델로 선정했습니다.

> 평가에서 선정한 모델과 현재 `.env.example`의 기본 모델은 다를 수 있습니다. 운영 환경의 비용과 제공자 지원 여부에 맞게 `LLM_MODEL`을 설정하세요.

## 문제 해결

### `/ui`에서 화면이 열리지 않는 경우

- `paran-auto-report/`와 `paran-auto-report-fe/`가 같은 상위 폴더에 있는지 확인합니다.
- 서버 시작 로그에 오류가 없는지 확인합니다.
- <http://localhost:8000/health>가 `{"status":"ok"}`를 반환하는지 확인합니다.

### Notion 데이터가 수집되지 않는 경우

- 대상 페이지와 데이터베이스에 Notion 통합이 연결되어 있는지 확인합니다.
- `.env`의 토큰과 ID에 공백이나 오타가 없는지 확인합니다.
- 여러 ID를 입력할 때 쉼표로 구분했는지 확인합니다.

### 모델을 변경한 뒤 검색 오류가 발생하는 경우

임베딩 모델을 변경했다면 Vector DB를 다시 구축합니다.

```bash
python scripts/build_vectordb.py build --force
```

### 실제 파일 미리보기가 실패하는 경우

`pandoc --version`으로 pandoc 설치 여부를 확인합니다. pandoc이 없어도 기본 보고서 생성과 Word 내보내기는 사용할 수 있지만, 실제 파일 미리보기와 PDF 변환은 제한될 수 있습니다.
