"""charts.py 그래프 도구 + chart_spec.py 빌더 테스트 (실 DB read-only).

실행:
    cd /home/koopark/claude/SignalForge/mcp-server
    DATABASE_URL='postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge' \
        .venv/bin/python -m pytest tests/test_charts.py -v
"""
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://signalforge:signalforge_pass@127.0.0.1:5434/signalforge",
)
from tools.chart_spec import (  # noqa: E402
    build_line, build_bar, build_pie, build_timeline, build_graph,
    chart_response, SERIES_COLORS,
)
from tools.charts import (  # noqa: E402
    chart_sentiment_timeseries_tool, chart_country_distribution_tool,
    chart_category_distribution_tool, chart_crisis_timeline_tool,
    chart_keyword_network_tool,
)

STD_KEYS = {"chart_type", "raw", "echarts_option", "summary"}


# ── chart_spec 순수함수 (DB 불요) ──────────────────────────────
def test_palette_synced_with_chartTheme():
    # chartTheme.ts palette.primary == #0072B2 (동기화 검증)
    assert SERIES_COLORS[0] == "#0072B2"


def test_builders_json_serializable():
    charts = [
        build_line(["1월", "2월"], [{"name": "A", "data": [1, 2]}], "L"),
        build_bar(["a", "b"], [3, 4], "B", horizontal=True),
        build_pie(["x", "y"], [5, 6], "P"),
        build_timeline(["d1", "d2"], [7, 8], "T", markers=[{"coord": ["d1", 7], "name": "p"}]),
        build_graph([{"id": "n1", "name": "k", "value": 9, "category": 0}],
                    [{"source": "n1", "target": "n1", "value": 1}], "G"),
    ]
    for opt in charts:
        s = json.dumps(opt)  # JS 함수 미포함 → 직렬화 통과
        assert "series" in opt and "color" in opt
        assert "function" not in s.lower()  # formatter JS 누출 없음


# ── 그래프 도구 (실 DB) ────────────────────────────────────────
# DB engine 이 단일 event loop 에 바인딩되므로 모든 async 검증을 한 run() 에 묶음
# (test_insights.py 패턴 — 테스트별 asyncio.run 은 'Event loop is closed' 유발).
async def _run_async_checks():
    # ① 시계열
    r = await chart_sentiment_timeseries_tool(["GS25", "GZF8"], days=90)
    assert set(r.keys()) == STD_KEYS and r["chart_type"] == "line"
    json.dumps(r["echarts_option"])
    assert len(r["echarts_option"]["series"]) == 2

    # ② 국가 분포
    r = await chart_country_distribution_tool(top_n=10)
    assert set(r.keys()) == STD_KEYS and r["chart_type"] == "bar"
    assert len(r["raw"]) <= 10 and r["raw"][0]["voc_count"] > 0

    # ② 카테고리 분포
    r = await chart_category_distribution_tool(top_n=10)
    assert set(r.keys()) == STD_KEYS
    json.dumps(r["echarts_option"])

    # ③ 위기 정합 (backend /deep/crisis-cases 와 동일 정의)
    r = await chart_crisis_timeline_tool("GN7")
    assert set(r.keys()) == STD_KEYS
    raw = r["raw"]
    assert raw["code"] == "GN7" and raw["total_voc"] > 0
    assert len(raw["timeline"]) > 0 and 0.0 <= raw["neg_rate"] <= 1.0

    # ③ 전체 위기 (CRISIS_CATALOG 5건)
    r = await chart_crisis_timeline_tool()
    assert isinstance(r["raw"], list) and len(r["raw"]) == 5

    # ④ 키워드 네트워크 (Tier 2 — force-graph)
    r = await chart_keyword_network_tool(days=30, min_cooccur=3, max_nodes=40)
    assert set(r.keys()) == STD_KEYS and r["chart_type"] == "graph"
    json.dumps(r["echarts_option"])
    assert r["echarts_option"]["series"][0]["type"] == "graph"
    assert r["raw"]["meta"]["node_count"] <= 40


def test_async_chart_tools():
    asyncio.run(_run_async_checks())


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
