"""insight.quality_report 단위 테스트.

순수함수 2케이스:
    1) collect_grounding_stats — 임시 insight_*.md 의 footer 점수 파싱
    2) render_markdown — 4개 신호가 모두 섹션으로 들어가는지 + 60분 초과 MV 경고
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

# crawler/ 를 sys.path 에 추가 — 단독 실행 시 import 보장
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.quality_report import (  # noqa: E402
    CacheStats,
    GroundingStats,
    MVStats,
    PerfStats,
    QualityReport,
    collect_grounding_stats,
    render_markdown,
)


# --- 1) grounding 점수 파싱 ----------------------------------------------
def test_collect_grounding_stats_parses_footer(tmp_path: Path):
    """insight_YYYY-MM-DD.md footer 의 'grounding score: 0.42' 를 추출한다."""
    target = date(2026, 6, 2)
    body = (
        "# insight\n\n어쩌고 분석 본문\n\n---\n"
        "_LLM grounding score: 0.42 (provider: ollama)_\n"
    )
    (tmp_path / f"insight_{target.isoformat()}.md").write_text(body, encoding="utf-8")

    stats = collect_grounding_stats(tmp_path, target, window_days=1)
    assert stats.scores == [0.42]
    assert stats.avg == 0.42
    assert stats.minimum == 0.42 and stats.maximum == 0.42
    assert stats.files == [f"insight_{target.isoformat()}.md"]
    assert stats.days_inspected == 1


def test_collect_grounding_stats_missing_file_returns_empty(tmp_path: Path):
    """대상 일자 파일이 없으면 빈 결과 + days_inspected=0."""
    target = date(2026, 1, 1)
    stats = collect_grounding_stats(tmp_path, target, window_days=1)
    assert stats.scores == []
    assert stats.avg is None
    assert stats.days_inspected == 0


# --- 2) Markdown 렌더링 --------------------------------------------------
def test_render_markdown_includes_all_sections_and_flags_stale_mv():
    """네 섹션 헤더가 모두 나오고, 60분 초과 MV 가 경고 플래그를 받는다."""
    report = QualityReport(
        target_date=date(2026, 6, 2),
        cache=CacheStats(enabled=True, hits=940, misses=60, ratio=0.94),
        grounding=GroundingStats(
            days_inspected=1,
            scores=[0.45],
            avg=0.45, minimum=0.45, maximum=0.45,
            files=["insight_2026-06-01.md"],
        ),
        mvs=[
            # fresh MV
            MVStats(
                name="mv_voc_daily",
                last_refresh=datetime.now(timezone.utc),
                age_minutes=12.3,
            ),
            # stale MV (>60 분)
            MVStats(
                name="kg_edges_daily",
                last_refresh=datetime(2026, 6, 1, tzinfo=timezone.utc),
                age_minutes=1500.0,
            ),
            # 에러 MV
            MVStats(name="country_daily", error="not found"),
        ],
        perf=PerfStats(
            endpoints=29,
            p95_under_200ms=28,
            over_threshold=[{"endpoint": "dashboard.overview", "p95": 238.4}],
        ),
    )

    md = render_markdown(report)

    # 1) 헤더
    assert "## 1. Redis 캐시" in md
    assert "## 2. LLM grounding 점수" in md
    assert "## 3. 머티리얼라이즈드 뷰 신선도" in md
    assert "## 4. Endpoint p95 (≤ 200ms 기준)" in md

    # 2) 캐시 수치 인용
    assert "hits = **940**" in md
    assert "94.00%" in md

    # 3) MV 경고 플래그
    assert "⚠ 1시간 초과" in md
    assert "⚠ not found" in md

    # 4) p95 초과 endpoint 명시
    assert "dashboard.overview" in md
    assert "28 / 29" in md
