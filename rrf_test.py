"""
LangChain Reciprocal Rank Fusion (RRF) 기본 테스트
- BM25Retriever + TFIDFRetriever를 EnsembleRetriever로 결합
- 외부 인프라(Qdrant 등) 없이 인메모리로 동작
"""

from langchain_community.retrievers import BM25Retriever, TFIDFRetriever
from langchain_classic.retrievers import EnsembleRetriever
from langchain_core.documents import Document

# 샘플 문서
docs = [
    Document(page_content="RAG(Retrieval-Augmented Generation)는 검색과 생성을 결합한 AI 시스템입니다.", metadata={"source": "doc1"}),
    Document(page_content="벡터 데이터베이스는 임베딩 벡터를 저장하고 유사도 검색을 지원합니다.", metadata={"source": "doc2"}),
    Document(page_content="BM25는 키워드 기반 검색 알고리즘으로 TF-IDF를 개선한 방식입니다.", metadata={"source": "doc3"}),
    Document(page_content="임베딩 모델은 텍스트를 고차원 벡터 공간으로 변환합니다.", metadata={"source": "doc4"}),
    Document(page_content="Qdrant는 고성능 벡터 검색 엔진으로 RAG 시스템에 자주 사용됩니다.", metadata={"source": "doc5"}),
    Document(page_content="RRF(Reciprocal Rank Fusion)는 여러 검색 결과를 순위 기반으로 통합합니다.", metadata={"source": "doc6"}),
    Document(page_content="LangChain EnsembleRetriever는 RRF를 사용해 다수의 retriever를 결합합니다.", metadata={"source": "doc7"}),
    Document(page_content="하이브리드 검색은 키워드와 시맨틱 검색을 함께 사용하여 성능을 높입니다.", metadata={"source": "doc8"}),
]

# BM25 Retriever (키워드 기반)
bm25 = BM25Retriever.from_documents(docs, k=4)

# TFIDF Retriever
tfidf = TFIDFRetriever.from_documents(docs, k=4)

# EnsembleRetriever - RRF로 결합 (c=60은 RRF 기본 상수)
ensemble = EnsembleRetriever(
    retrievers=[bm25, tfidf],
    weights=[0.5, 0.5],
    c=60
)

# 테스트
queries = [
    "RAG 시스템에서 검색은 어떻게 동작하나요?",
    "벡터 데이터베이스와 임베딩",
    "RRF 알고리즘이란",
]

print("=" * 60)
print("LangChain RRF (EnsembleRetriever) 테스트")
print("=" * 60)

for query in queries:
    print(f"\n쿼리: {query}")
    print("-" * 40)
    results = ensemble.invoke(query)
    for i, doc in enumerate(results, 1):
        print(f"[{i}] ({doc.metadata['source']}) {doc.page_content[:60]}...")

print("\n✅ 테스트 완료")
