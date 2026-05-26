#!/usr/bin/env python3
"""
Embedding × LLM 단계별 평가 스크립트 (Retriever=rrf 고정).

사용법:
  cd paran-auto-report

  # 1단계: embedding 비교 (LLM=gpt-4.1 고정)
  python -m experiments.evaluate --stage 1

  # 2단계: LLM 비교 (embedding 고정)
  python -m experiments.evaluate --stage 2 --embedding openai-large

  # 필터 옵션
  python -m experiments.evaluate --stage 1 --embeddings openai-large qwen3-8b
  python -m experiments.evaluate --stage 2 --embedding qwen3-8b --llms gpt-5-mini deepseek-v4-flash
  python -m experiments.evaluate --stage 1 --weeks 1 --limit 3 --dry-run
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import yaml
from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import OPENAI_API_KEY, OPENAI_BASE_URL, QDRANT_PATH, QDRANT_COLLECTION, PLAN_PAGE_ID
from src.ingestion.embedder import OpenAIEmbedder
from src.report.generator import REPORT_PROMPT, _fetch_plan_context, _build_qdrant_filter, _format_docs, _build_date_range_info, _build_user_field
from langfuse.langchain import CallbackHandler as LangfuseCallbackHandler

from experiments.retrievers import get_retriever
from experiments.docx_extractor import extract_text
from experiments.llm_judge import judge

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "eval_config.yaml"
RESULTS_DIR = Path(__file__).resolve().parent / "results"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)


def load_vs_for_embedding(emb_cfg: dict) -> QdrantVectorStore:
    path = emb_cfg.get("qdrant_path") or QDRANT_PATH
    coll = emb_cfg.get("collection") or QDRANT_COLLECTION
    if emb_cfg.get("qdrant_path"):
        path = str(BASE_DIR / emb_cfg["qdrant_path"])
    embedder = OpenAIEmbedder(api_key=OPENAI_API_KEY, model=emb_cfg["model"], base_url=OPENAI_BASE_URL)
    return QdrantVectorStore(client=QdrantClient(path=path), collection_name=coll, embedding=embedder)


def generate_report_for_eval(
    vs: QdrantVectorStore,
    llm_model: str,
    retriever_cfg: dict,
    topic: str,
    since: datetime | None,
    until: datetime | None,
) -> str:
    qdrant_filter = _build_qdrant_filter(since, until)

    retriever = get_retriever(
        vs=vs,
        retriever_type=retriever_cfg["type"],
        k=retriever_cfg["k"],
        qdrant_filter=qdrant_filter,
        llm_model=llm_model,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
    )

    docs = retriever.invoke(topic)
    context = _format_docs(docs)
    plan_context = _fetch_plan_context(vs)
    date_range_info = _build_date_range_info(since, until)

    handler = LangfuseCallbackHandler(trace_context={"session_id": f"eval-{topic}"})
    llm = ChatOpenAI(
        model=llm_model,
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
        temperature=0,
    )
    chain = REPORT_PROMPT | llm | StrOutputParser()
    return chain.invoke(
        {
            "topic": topic,
            "context": context,
            "date_range_info": date_range_info,
            "plan_context": plan_context,
            "role_info": "",
            "role_instruction": "",
            "team_name": _build_user_field(None),
            "student_id": _build_user_field(None),
            "department": _build_user_field(None),
            "name": _build_user_field(None),
        },
        config={"callbacks": [handler]},
    )


def _call_judge(j_cfg: dict, generated: str, reference_text: str) -> dict:
    score = judge(
        generated=generated,
        reference=reference_text,
        model=j_cfg["model"],
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_BASE_URL,
    )
    return {"judge": j_cfg["name"], **score.to_dict()}


def run_combination(
    emb_cfg: dict,
    llm_cfg: dict,
    tc: dict,
    vs: QdrantVectorStore,
    retriever_cfg: dict,
    judge_configs: list,
) -> dict | None:
    week = tc["week"]
    since = parse_date(tc.get("since"))
    until = parse_date(tc.get("until"))
    label = f"[{emb_cfg['name']} × {llm_cfg['name']} / {week}주차]"

    print(f"  📝 {label} 보고서 생성 중...")
    try:
        generated = generate_report_for_eval(
            vs=vs,
            llm_model=llm_cfg["model"],
            retriever_cfg=retriever_cfg,
            topic=tc["topic"],
            since=since,
            until=until,
        )
    except Exception as e:
        print(f"  ⚠️  {label} 생성 실패: {e}")
        return None

    ref_path = BASE_DIR / tc["reference"]
    if not ref_path.exists():
        print(f"  ⚠️  {label} reference 없음: {ref_path}")
        return None

    reference_text = extract_text(ref_path)

    print(f"  ⚖️  {label} Judge 채점 중 (3개 병렬)...")
    judge_scores: list[dict] = []
    with ThreadPoolExecutor(max_workers=len(judge_configs)) as ex:
        futures = {ex.submit(_call_judge, j_cfg, generated, reference_text): j_cfg for j_cfg in judge_configs}
        for fut in as_completed(futures):
            j_cfg = futures[fut]
            try:
                score_dict = fut.result()
                judge_scores.append(score_dict)
                s = score_dict
                print(
                    f"    {label} / {j_cfg['name']}: "
                    f"내용={s['content_similarity']} 문체={s['style_similarity']} "
                    f"완성={s['completeness']} overall={s['overall']}"
                )
            except Exception as e:
                print(f"    ⚠️  {label} / {j_cfg['name']} 실패: {e}")

    if not judge_scores:
        return None

    score_keys = ["content_similarity", "format_compliance", "style_similarity", "completeness", "overall"]
    avg_scores = {k: round(sum(s[k] for s in judge_scores) / len(judge_scores), 2) for k in score_keys}
    print(f"  ✅ {label} Judge 평균: {avg_scores}")

    return {
        "embedding": emb_cfg["name"],
        "generation_llm": llm_cfg["name"],
        "retriever": retriever_cfg["type"],
        "week": week,
        "judge_details": judge_scores,
        **{f"avg_{k}": v for k, v in avg_scores.items()},
        "generated_preview": generated[:300],
    }


def build_summary(all_scores: list[dict], group_keys: list[str], judge_configs: list) -> list[dict]:
    score_keys = ["content_similarity", "format_compliance", "style_similarity", "completeness", "overall"]

    groups: dict[tuple, list[dict]] = {}
    for r in all_scores:
        key = tuple(r[k] for k in group_keys)
        groups.setdefault(key, []).append(r)

    summary = []
    for key, results in groups.items():
        avgs = {
            f"avg_{k}": round(sum(r[f"avg_{k}"] for r in results) / len(results), 2)
            for k in score_keys
        }
        per_judge: dict[str, dict] = {}
        for j_cfg in judge_configs:
            j_name = j_cfg["name"]
            j_scores = [s for r in results for s in r["judge_details"] if s["judge"] == j_name]
            if j_scores:
                per_judge[j_name] = {
                    k: round(sum(s[k] for s in j_scores) / len(j_scores), 2) for k in score_keys
                }
        entry = {k: v for k, v in zip(group_keys, key)}
        entry["weeks_evaluated"] = len(results)
        entry.update(avgs)
        entry["per_judge"] = per_judge
        summary.append(entry)

    return summary


def print_summary_table(summary: list[dict], group_keys: list[str]) -> None:
    sorted_results = sorted(summary, key=lambda r: r["avg_overall"], reverse=True)

    label_width = 36
    print("\n" + "=" * 70)
    print("📊 평가 결과 랭킹  (Judge 3개 평균, format 제외 3기준)")
    print("=" * 70)
    header = f"{'조합':<{label_width}} {'내용':>6} {'문체':>6} {'완성':>6} {'평균':>6}"
    print(header)
    print("-" * 70)
    for rank, r in enumerate(sorted_results, 1):
        label = " × ".join(str(r[k]) for k in group_keys)
        print(
            f"{rank:>2}. {label:<{label_width - 4}} "
            f"{r['avg_content_similarity']:>6.2f} "
            f"{r['avg_style_similarity']:>6.2f} "
            f"{r['avg_completeness']:>6.2f} "
            f"{r['avg_overall']:>6.2f}"
        )
    print("=" * 70)


def save_results(all_scores: list[dict], summary: list[dict], timestamp: str, stage: int) -> None:
    RESULTS_DIR.mkdir(exist_ok=True)

    json_path = RESULTS_DIR / f"{timestamp}_stage{stage}_results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_scores, f, ensure_ascii=False, indent=2)

    csv_path = RESULTS_DIR / f"{timestamp}_stage{stage}_summary.csv"
    if summary:
        flat_rows = []
        for r in summary:
            row = {k: v for k, v in r.items() if k != "per_judge"}
            for j_name, j_scores in r.get("per_judge", {}).items():
                for metric, val in j_scores.items():
                    row[f"{j_name}__{metric}"] = val
            flat_rows.append(row)
        all_keys = list(dict.fromkeys(k for row in flat_rows for k in row))
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(flat_rows)

    print(f"\n💾 상세 결과: {json_path}")
    print(f"💾 요약 CSV:  {csv_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Embedding × LLM 단계별 평가")
    parser.add_argument("--stage", type=int, choices=[1, 2], default=1,
                        help="1=embedding 선정(LLM고정), 2=LLM 선정(embedding고정)")
    parser.add_argument("--embedding", help="2단계에서 고정할 embedding name")
    parser.add_argument("--embeddings", nargs="*", help="1단계 평가할 embedding name 필터")
    parser.add_argument("--llms", nargs="*", help="2단계 평가할 LLM name 필터")
    parser.add_argument("--weeks", nargs="*", type=int, help="평가할 주차 번호 목록")
    parser.add_argument("--workers", type=int, default=4, help="병렬 worker 수 (기본 4)")
    parser.add_argument("--limit", type=int, default=None, help="실행할 최대 조합 수")
    parser.add_argument("--dry-run", action="store_true", help="조합 목록만 출력하고 종료")
    args = parser.parse_args()

    cfg = load_config()
    judge_configs = cfg["judges"]
    retriever_cfg = cfg["fixed_retriever"]

    test_cases = cfg["test_cases"]
    if args.weeks:
        test_cases = [t for t in test_cases if t["week"] in args.weeks]
    # 1~4주차만 사용
    test_cases = [t for t in test_cases if t["week"] in {1, 2, 3, 4}]

    if args.stage == 1:
        embedding_configs = cfg["embeddings"]
        if args.embeddings:
            embedding_configs = [e for e in embedding_configs if e["name"] in args.embeddings]
        llm_configs = [cfg["stage1_llm"]]
        group_keys = ["embedding", "generation_llm"]
        stage_label = f"1단계 (LLM={cfg['stage1_llm']['name']} 고정)"
    else:
        if not args.embedding:
            parser.error("--stage 2 에는 --embedding <name> 이 필요합니다.")
        emb_name = args.embedding
        emb_matches = [e for e in cfg["embeddings"] if e["name"] == emb_name]
        if not emb_matches:
            parser.error(f"embedding '{emb_name}' 을 eval_config.yaml에서 찾을 수 없습니다.")
        embedding_configs = emb_matches
        llm_configs = cfg["generation_llms"]
        if args.llms:
            llm_configs = [l for l in llm_configs if l["name"] in args.llms]
        group_keys = ["embedding", "generation_llm"]
        stage_label = f"2단계 (embedding={emb_name} 고정)"

    combinations = [
        (emb_cfg, llm_cfg, tc)
        for tc in test_cases
        for emb_cfg in embedding_configs
        for llm_cfg in llm_configs
    ]
    if args.limit:
        combinations = combinations[: args.limit]

    judge_names = [j["name"] for j in judge_configs]
    print(f"🔬 평가 시작: {stage_label}")
    print(f"   조합 수: {len(embedding_configs)}개 Embedding × {len(llm_configs)}개 LLM × {len(test_cases)}개 주차 = {len(combinations)}회 생성")
    print(f"   Judge:   {', '.join(judge_names)}")
    print(f"   Retriever: {retriever_cfg['type']} (k={retriever_cfg['k']})")

    if args.dry_run:
        for emb_cfg, llm_cfg, tc in combinations:
            print(f"  - {emb_cfg['name']} × {llm_cfg['name']} / {tc['week']}주차")
        return

    # Vectorstore를 embedding별로 1회 로드 (스레드 안전)
    print("\n📂 VectorStore 로딩...")
    vs_map: dict[str, QdrantVectorStore] = {}
    for emb_cfg in embedding_configs:
        print(f"   [{emb_cfg['name']}] 로딩 중...")
        vs_map[emb_cfg["name"]] = load_vs_for_embedding(emb_cfg)
    print("   완료\n")

    all_scores: list[dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_combo = {
            executor.submit(
                run_combination,
                emb_cfg, llm_cfg, tc,
                vs_map[emb_cfg["name"]],
                retriever_cfg,
                judge_configs,
            ): (emb_cfg, llm_cfg, tc)
            for emb_cfg, llm_cfg, tc in combinations
        }
        for future in as_completed(future_to_combo):
            emb_cfg, llm_cfg, tc = future_to_combo[future]
            try:
                result = future.result()
                if result:
                    all_scores.append(result)
            except Exception as e:
                print(f"  ⚠️  [{emb_cfg['name']} × {llm_cfg['name']} / {tc['week']}주차] 예외: {e}")

    summary = build_summary(all_scores, group_keys, judge_configs)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_results(all_scores, summary, timestamp, args.stage)
    print_summary_table(summary, group_keys)


if __name__ == "__main__":
    main()
