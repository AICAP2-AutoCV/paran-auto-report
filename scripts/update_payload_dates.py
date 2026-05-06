"""Qdrant 재생성 없이 prop_날짜 메타데이터만 업데이트."""

import sys
from pathlib import Path
from datetime import date, datetime, time, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from notion_client import Client as NotionClient
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, SetPayload

from src.config import NOTION_TOKEN, NOTION_SOURCE_DATABASES, QDRANT_PATH, QDRANT_COLLECTION


def normalize_datetime_payload(value: str) -> str:
    """Notion date 값을 Qdrant DatetimeRange가 읽을 수 있는 RFC3339 문자열로 변환."""
    if len(value) == 10:
        dt = datetime.combine(date.fromisoformat(value), time.min, tzinfo=timezone.utc)
    else:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def fetch_page_dates() -> dict[str, str]:
    """Notion DB에서 page_id → 날짜 매핑 반환."""
    client = NotionClient(auth=NOTION_TOKEN)
    page_dates = {}

    for db_id in NOTION_SOURCE_DATABASES:
        cursor = None
        while True:
            kwargs = {"page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = client.data_sources.query(db_id, **kwargs)
            for page in resp["results"]:
                page_id = page["id"]
                props = page.get("properties", {})
                date_prop = props.get("날짜") or props.get("date") or props.get("Date")
                if date_prop and date_prop.get("type") == "date":
                    date_val = date_prop.get("date") or {}
                    start = date_val.get("start")
                    if start:
                        page_dates[page_id] = normalize_datetime_payload(start)
            if not resp.get("has_more"):
                break
            cursor = resp["next_cursor"]

    return page_dates


def update_qdrant_dates(page_dates: dict[str, str]) -> int:
    """Qdrant 포인트에 prop_날짜 페이로드 추가. 업데이트된 포인트 수 반환."""
    qdrant = QdrantClient(path=QDRANT_PATH)
    updated = 0

    for page_id, date_str in page_dates.items():
        # 해당 page_id를 가진 모든 포인트 조회
        points, _ = qdrant.scroll(
            collection_name=QDRANT_COLLECTION,
            scroll_filter=Filter(must=[
                FieldCondition(key="metadata.page_id", match=MatchValue(value=page_id))
            ]),
            limit=100,
            with_payload=False,
            with_vectors=False,
        )
        if not points:
            continue

        point_ids = [p.id for p in points]
        qdrant.set_payload(
            collection_name=QDRANT_COLLECTION,
            payload={"metadata": {"prop_날짜": date_str}},
            points=point_ids,
        )
        updated += len(point_ids)
        print(f"  {page_id[:8]}... → {date_str} ({len(point_ids)}개 청크)")

    return updated


def main():
    print("📅 Notion에서 날짜 컬럼 조회 중...")
    page_dates = fetch_page_dates()
    print(f"  {len(page_dates)}개 페이지에서 날짜 확인\n")

    if not page_dates:
        print("⚠️  날짜 데이터 없음. Notion DB에 '날짜' 컬럼이 있는지 확인하세요.")
        return

    print("💾 Qdrant 페이로드 업데이트 중...")
    updated = update_qdrant_dates(page_dates)
    print(f"\n✅ 완료: {updated}개 청크 업데이트")


if __name__ == "__main__":
    main()
