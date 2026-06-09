"""
exports.csv_export / exports.products_csv 단위 테스트.

실 DB 사용 (read-only). LIMIT 으로 부하 최소화.

실행:
  cd /home/koopark/claude/SignalForge/crawler && \
    DATABASE_URL=postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge \
    ../.venv/bin/python -m pytest tests/test_exports.py -v
"""
import csv
import gzip
import io
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from exports.csv_export import (
    export_voc_csv,
    _build_query,
    _serialize_categories,
    CSV_COLUMNS,
)
from exports.products_csv import (
    export_products_csv,
    CSV_COLUMNS as PRODUCTS_CSV_COLUMNS,
    EXCERPT_LIMIT,
)


# -------------------- 순수 함수 (DB 없이) --------------------

def test_serialize_categories_handles_none_list_string():
    assert _serialize_categories(None) == ""
    assert _serialize_categories([]) == ""
    assert _serialize_categories(["battery", "camera"]) == "battery|camera"
    assert _serialize_categories(("a", "b", "c")) == "a|b|c"
    assert _serialize_categories("display") == "display"


def test_build_query_filters_compose_correctly():
    sql, params = _build_query(days=7, platform_code="reddit", product_code="GS24U")
    assert "v.collected_at >= NOW()" in sql
    assert "p.code = :platform_code" in sql
    assert "pr.code = :product_code" in sql
    assert params == {"days": "7", "platform_code": "reddit", "product_code": "GS24U"}

    sql2, params2 = _build_query(days=None, platform_code=None, product_code=None)
    # 필터 조건은 사라져야 함 (SELECT 절의 v.collected_at AS 는 항상 존재)
    assert "v.collected_at >= NOW()" not in sql2
    assert ":platform_code" not in sql2
    assert ":product_code" not in sql2
    assert params2 == {}


def test_csv_columns_schema_locked():
    """외부 소비자 계약 — 컬럼 순서/이름은 깨면 안 됨."""
    assert CSV_COLUMNS == [
        "id",
        "product_code",
        "platform_code",
        "country_code",
        "content_original",
        "content_translated",
        "sentiment_score",
        "sentiment_label",
        "categories",
        "published_at",
        "collected_at",
    ]


def test_products_csv_columns_includes_excerpts():
    base = PRODUCTS_CSV_COLUMNS[:8]
    assert base == [
        "product_code", "product_name_en", "product_name_ko",
        "rows", "sentiment_pos", "sentiment_neg", "sentiment_neu",
        "top_categories",
    ]
    assert len(PRODUCTS_CSV_COLUMNS) == 8 + EXCERPT_LIMIT


# -------------------- 통합 (실 DB read-only) --------------------

_HAS_DB = bool(os.getenv("DATABASE_URL"))
_SKIP_REASON = "DATABASE_URL 미설정 — 통합 테스트 skip"


@pytest.mark.skipif(not _HAS_DB, reason=_SKIP_REASON)
def test_voc_csv_export_smoke(tmp_path: Path):
    """최근 1일 + reddit 만 → gzip CSV 생성 + 헤더/행 검증."""
    import asyncio

    result = asyncio.run(export_voc_csv(
        days=1,
        platform_code="reddit",
        out_dir=tmp_path,
        batch=500,
        filename="voc-smoke.csv.gz",
    ))
    path = Path(result["path"])
    assert path.exists()
    assert path.suffix == ".gz"
    assert result["bytes"] > 0

    # 실제 gzip 으로 열어 헤더/한 행 검증
    with gzip.open(path, mode="rt", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        assert header == CSV_COLUMNS
        # rows 수와 일치하는지 (헤더 제외)
        count = sum(1 for _ in reader)
        assert count == result["rows"]


@pytest.mark.skipif(not _HAS_DB, reason=_SKIP_REASON)
def test_products_csv_export_smoke(tmp_path: Path):
    """제품 48종 전체 → CSV 생성 + 컬럼 / 행 검증."""
    import asyncio

    result = asyncio.run(export_products_csv(
        out_dir=tmp_path,
        filename="products-smoke.csv",
    ))
    path = Path(result["path"])
    assert path.exists()
    assert result["rows"] >= 1

    with path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        assert header == PRODUCTS_CSV_COLUMNS
        first = next(reader, None)
        assert first is not None, "products 테이블이 비어있어선 안 됨"
        # rows 컬럼은 정수 문자열
        rows_col_idx = PRODUCTS_CSV_COLUMNS.index("rows")
        int(first[rows_col_idx])
