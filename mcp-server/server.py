"""
SignalForge MCP Server
FastMCP 기반 — Claude가 VOC 데이터베이스와 직접 대화
"""
from mcp.server.fastmcp import FastMCP
from typing import Optional
import os, asyncio

from tools.query import query_voc_tool, get_top_issues_tool, search_voc_tool
from tools.analytics import (
    analyze_sentiment_trend_tool,
    compare_products_tool,
    get_country_breakdown_tool,
    get_voc_summary_tool,
)
from tools.charts import (
    chart_sentiment_timeseries_tool,
    chart_country_distribution_tool,
    chart_category_distribution_tool,
    chart_crisis_timeline_tool,
    chart_keyword_network_tool,
)
from tools.insights import (
    daily_briefing_tool,
    alert_check_tool,
    site_health_tool,
    top_emerging_keywords_tool,
)

# @lat: mcp — [[mcp-server]] 참조. 7개 도구 정의.
mcp = FastMCP(
    name="SignalForge VOC Intelligence",
    instructions="Samsung Galaxy 제품군 VOC 데이터베이스에 질문하는 MCP 서버",
    port=int(os.getenv("MCP_PORT", "8001")),
)


# ── VOC 조회 ───────────────────────────────────────────────

@mcp.tool()
async def query_voc(
    product_code: str,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
    limit: int = 20,
) -> list:
    """
    특정 제품의 VOC를 조건별로 조회합니다.

    Args:
        product_code: 제품 코드 (예: GS25U, GZF7, GZFL7)
        country: 국가 코드 (예: KR, US, DE) — 선택
        category: 카테고리 코드 (battery, camera, display, performance, software, build_quality, price, design, connectivity, ai_features, accessories, comparison) — 선택
        sentiment: 감성 필터 (positive, negative, neutral) — 선택
        limit: 반환 건수 (기본 20, 최대 100)
    """
    return await query_voc_tool(product_code, country, category, sentiment, min(limit, 100))


@mcp.tool()
async def get_top_issues(
    product_code: str,
    period_days: int = 30,
    top_n: int = 10,
) -> list:
    """
    지난 N일간 특정 제품에서 가장 많이 언급된 이슈 TOP N을 반환합니다.

    Args:
        product_code: 제품 코드 (예: GS25U)
        period_days: 분석 기간 (일, 기본 30)
        top_n: 상위 몇 개 이슈 (기본 10, 최대 20)
    """
    return await get_top_issues_tool(product_code, period_days, min(top_n, 20))


@mcp.tool()
async def search_voc(
    keyword: str,
    product_code: Optional[str] = None,
    limit: int = 30,
) -> list:
    """
    키워드로 VOC 전문 검색을 수행합니다 (PostgreSQL FTS 기반).

    Args:
        keyword: 검색 키워드 (영어 권장)
        product_code: 특정 제품으로 범위 한정 — 선택
        limit: 반환 건수 (기본 30)
    """
    return await search_voc_tool(keyword, product_code, limit)


# ── 분석 ──────────────────────────────────────────────────

@mcp.tool()
async def analyze_sentiment_trend(
    product_code: str,
    period_days: int = 90,
    granularity: str = "week",
) -> dict:
    """
    제품의 감성 점수 시계열 트렌드를 분석합니다.

    Args:
        product_code: 제품 코드
        period_days: 분석 기간 (일, 기본 90)
        granularity: 집계 단위 (day, week, month — 기본 week)
    """
    return await analyze_sentiment_trend_tool(product_code, period_days, granularity)


@mcp.tool()
async def compare_products(
    product_codes: list[str],
    category: Optional[str] = None,
) -> dict:
    """
    여러 제품 간 VOC를 비교합니다.

    Args:
        product_codes: 비교할 제품 코드 리스트 (예: ["GS25U", "GZF7"])
        category: 특정 카테고리로 범위 한정 — 선택
    """
    return await compare_products_tool(product_codes, category)


@mcp.tool()
async def get_country_breakdown(
    product_code: str,
    period_days: int = 30,
) -> list:
    """
    제품의 국가별 VOC 건수 및 감성 분포를 조회합니다.

    Args:
        product_code: 제품 코드
        period_days: 분석 기간 (일)
    """
    return await get_country_breakdown_tool(product_code, period_days)


@mcp.tool()
async def get_voc_summary(
    product_code: str,
    period_days: int = 7,
) -> str:
    """
    제품의 최근 VOC 요약 텍스트를 생성합니다.

    Args:
        product_code: 제품 코드
        period_days: 요약 기간 (일, 기본 7)
    """
    return await get_voc_summary_tool(product_code, period_days)


# ── 인사이트 / 운영 도구 ──────────────────────────────────────────

@mcp.tool()
async def daily_briefing(date: Optional[str] = None) -> str:
    """
    지정 날짜(KST)의 VOC 일일 브리핑을 자연어로 생성합니다.

    전체 수집량, 감성 분포, 언급 TOP 제품, 핫 카테고리, 부정 다발 제품을
    한 문서로 요약합니다.

    Args:
        date: YYYY-MM-DD (예: '2026-06-01'). 생략 시 오늘(KST).
    """
    return await daily_briefing_tool(date)


@mcp.tool()
async def alert_check() -> dict:
    """
    현재 임계치 상태를 점검합니다.

    - 부정 비율 ≥40% & 24h 30건 이상인 제품
    - 24h 부정 건수가 직전 24h 대비 2배 이상 급증한 제품
    - 12시간 이상 신규 수집이 없는 플랫폼

    임계치와 함께 매칭된 항목 리스트를 반환합니다.
    """
    return await alert_check_tool()


@mcp.tool()
async def site_health() -> list:
    """
    플랫폼(사이트)별 최근 24시간 활동 현황을 반환합니다.

    각 플랫폼의 24h 수집량, 부정 건수, 평균 감성, 마지막 수집 시각,
    그리고 상태(healthy / quiet / stale / no_data_ever)를 포함합니다.
    """
    return await site_health_tool()


@mcp.tool()
async def top_emerging_keywords(
    period_days: int = 7,
    product_code: Optional[str] = None,
    top_n: int = 20,
) -> dict:
    """
    최근 N일간 VOC 본문을 토큰화하여 한국어/영어 키워드 빈도 TOP N 을 반환합니다.

    Args:
        period_days: 분석 기간 (일, 기본 7)
        product_code: 특정 제품으로 범위 한정 — 선택
        top_n: 언어별 반환 키워드 수 (기본 20, 최대 50)
    """
    return await top_emerging_keywords_tool(period_days, product_code, min(top_n, 50))


# ── 그래프 규격 도구 (r6+ 2026-06-12) — voc_active 기반, echarts_option 반환 ──
# 모든 도구가 {chart_type, raw, echarts_option, summary} 반환.
# LLM 은 echarts_option 을 그대로 렌더하거나 raw 로 다른 라이브러리에 매핑.
@mcp.tool()
async def chart_sentiment_timeseries(
    product_codes: list, days: int = 90, granularity: str = "week"
) -> dict:
    """제품별 VOC sentiment 시계열을 라인 차트 규격(ECharts option)으로 반환.

    Args:
        product_codes: 제품 코드 목록 (예: ["GS25","GZF8"]). 다제품 동시 비교.
        days: 조회 기간 (일, 기본 90)
        granularity: day / week / month (기본 week)
    Returns: {chart_type:"line", raw:{per_product,periods}, echarts_option, summary}
    """
    return await chart_sentiment_timeseries_tool(product_codes, days, granularity)


@mcp.tool()
async def chart_country_distribution(
    product_code: Optional[str] = None, top_n: int = 15
) -> dict:
    """국가별 VOC 분포를 가로 막대 차트 규격으로 반환. product_code 생략 시 전체."""
    return await chart_country_distribution_tool(product_code, top_n)


@mcp.tool()
async def chart_category_distribution(
    product_code: Optional[str] = None, top_n: int = 15
) -> dict:
    """카테고리별 VOC 분포를 가로 막대 차트 규격으로 반환. product_code 생략 시 전체."""
    return await chart_category_distribution_tool(product_code, top_n)


@mcp.tool()
async def chart_crisis_timeline(case_code: Optional[str] = None) -> dict:
    """위기 사례 일별 timeline 을 line+area 차트 규격으로 반환 (peak marker 포함).

    case_code 생략 시 5개 위기 사례 전체 raw + 첫 사례 차트.
    유효 code: GN7 / GZF1 / GS22U / GZFL3 / GS20.
    """
    return await chart_crisis_timeline_tool(case_code)


@mcp.tool()
async def chart_keyword_network(
    product_code: Optional[str] = None, days: int = 30,
    min_cooccur: int = 3, max_nodes: int = 40,
) -> dict:
    """키워드 동시출현 네트워크를 force-graph 차트 규격으로 반환 (union-find 군집).

    Args:
        product_code: 제품 한정 — 선택 (없으면 전체)
        days: 조회 기간 (일, 기본 30)
        min_cooccur: 엣지 최소 동시출현 횟수 (기본 3, 노이즈 컷)
        max_nodes: 최대 노드 수 (기본 40, degree 상위)
    Returns: {chart_type:"graph", raw:{nodes,edges,meta}, echarts_option, summary}
    """
    return await chart_keyword_network_tool(product_code, days, min_cooccur, max_nodes)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
