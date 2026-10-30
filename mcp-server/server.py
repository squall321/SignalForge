"""
SignalForge MCP Server
FastMCP 기반 — Claude가 VOC 데이터베이스와 직접 대화
"""
from mcp.server.fastmcp import FastMCP
from typing import Optional
import os, asyncio

from tools.query import (query_voc_tool, get_top_issues_tool, search_voc_detail,
                         resolve_product_keyword_tool)

# 시맨틱 검색은 crawler 의 정본 사전(/crawler 바인드)과 numpy(이미지)를 import 한다. 어느 하나가
# 빠진 채 재기동되면 여기서 실패하는데, 그 때문에 도구 전체가 죽으면 안 된다 —
# 검색은 예전 키워드 검색으로 돌리고, 원인을 로그와 결과(notes)에 남긴다.
# 원인은 예외 종류로 가른다 — 2026-10-27 cae00 은 numpy 가 없는 이미지였는데 문구가 '/crawler 바인드'
# 를 짚어 엉뚱한 곳을 보게 했다.
_CODE_MODULES = {"base", "nlp", "ontology", "keyword_match", "tools"}


def _semantic_import_note(err: Exception) -> str:
    name = (getattr(err, "name", None) or "").split(".")[0]
    if isinstance(err, ModuleNotFoundError) and name and name not in _CODE_MODULES:
        return (f"sf-mcp 이미지에 파이썬 패키지 '{name}' 가 없다 — mcp-server/requirements.txt 로 "
                f"apptainer/sif/mcp.sif 를 다시 지어야 한다(키워드 검색으로 찾았다)")
    if isinstance(err, ModuleNotFoundError) and name in _CODE_MODULES:
        return (f"코드 모듈 '{name}' 를 못 찾는다 — sf-mcp 에 /crawler·/shared 바인드가 있어야 한다"
                f"(scripts/up.sh 로 다시 띄운다, 키워드 검색으로 찾았다)")
    return f"온톨로지를 불러오지 못해 키워드 검색으로 찾았다 ({err!r})"


try:
    from tools.semantic import semantic_search_tool, get_ontology_tool
    print("[semantic] semantic import ok", flush=True)        # 기동 스모크 — up.sh 가 이 줄을 본다
except Exception as _sem_err:  # noqa: BLE001
    import logging as _logging
    _logging.getLogger(__name__).exception("시맨틱 검색 비활성 — 온톨로지 import 실패")
    _SEM_NOTE = _semantic_import_note(_sem_err)
    print(f"[semantic] semantic import FAILED — {_SEM_NOTE}", flush=True)

    async def semantic_search_tool(query, product_code=None, limit=30, days=None,
                                   order="relevance", match="auto"):
        d = await search_voc_detail(query, product_code, min(limit, 100), days,
                                    "recent" if order == "relevance" else order, match)
        return {"query": query, "strategy": "keyword", "match_mode": d["match_mode"],
                "interpretation": {"notes": [_SEM_NOTE] + d["notes"]}, "total": d["total"], "results": d["results"]}

    async def get_ontology_tool(concept=None):
        return {"error": _SEM_NOTE}
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
from tools.stats import (
    corpus_overview_tool, keyword_stats_tool,
    period_compare_tool, voc_breakdown_tool,
)
from tools.defects import (
    defect_timeline_tool, defect_breakdown_tool, defect_cooccurrence_tool,
    defect_onset_tool, defect_evidence_tool,
    defect_profile_tool,
    defect_anomalies_tool,
    lifecycle_compare_tool,
)
from tools.breakdowns import (
    get_platform_breakdown_tool,
    get_engagement_leaders_tool,
    get_language_breakdown_tool,
)
from tools.coverage import (
    get_product_timeline_tool,
    get_kg_relations_tool,
    get_unmapped_voc_tool,
)
from tools.pii import masked


class _MaskedFastMCP(FastMCP):
    """나가는 응답은 개인정보를 기본으로 가린다(SG-18 (가)) — 도구마다 잊지 않게 등록 길목에서 감싼다.
    원문(작성자·연락처·계정)은 SignalForge 화면에서 본다. 가리는 범위는 tools/pii.py."""

    def tool(self, *args, **kwargs):
        register = super().tool(*args, **kwargs)
        return lambda fn: register(masked(fn))


# @lat: mcp — [[mcp-server]] 참조. 7개 도구 정의.
mcp = _MaskedFastMCP(
    name="SignalForge VOC Intelligence",
    instructions="Samsung Galaxy 제품군 VOC 데이터베이스에 질문하는 MCP 서버",
    port=int(os.getenv("MCP_PORT", "8001")),
)


# ── VOC 조회 ───────────────────────────────────────────────

@mcp.tool()
async def query_voc(
    product_code: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[str] = None,
    sentiment: Optional[str] = None,
    platform: Optional[str] = None,
    keyword: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 20,
) -> list:
    """
    VOC를 조건별로 자유 조회합니다. 모든 필터가 선택이며 조합 가능합니다.
    product_code 를 생략하면 전체 제품(미태깅 포함)을 횡단 조회합니다.
    결과의 product_code 가 비어 있으면 본문에 특정 모델명이 없는 글입니다(일반 토론·짧은
    댓글·브랜드만 언급) — 처리 실패가 아닙니다. 현재 태깅률은 get_unmapped_voc 의 coverage.

    Args:
        product_code: 제품 코드 (예: GS25U, GZF7) — 생략 시 전체
        country: 국가 코드 (예: KR, US, DE) — 선택
        category: 카테고리 코드 (battery, camera, display, performance, software, build_quality, price, design, connectivity, ai_features, accessories, comparison) — 선택
        sentiment: 감성 필터 (positive, negative, neutral) — 선택
        platform: 플랫폼 코드 (예: reddit, youtube, gsmarena) — 선택
        keyword: 본문 전문검색 키워드 (영어 권장) — 선택
        start_date: 시작일 YYYY-MM-DD (published_at >=) — 선택
        end_date: 종료일 YYYY-MM-DD (published_at <, 반열림) — 선택
        limit: 반환 건수 (기본 20, 최대 100)
    """
    return await query_voc_tool(
        product_code, country, category, sentiment, platform,
        keyword, start_date, end_date, min(limit, 100))


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
    days: Optional[int] = None,
    order: str = "relevance",
    match: str = "auto",
    semantic: bool = True,
) -> dict:
    """
    VOC 를 **뜻으로** 검색합니다. 한국어·영어·섞어 써도 됩니다 — 온톨로지가 자동 해석합니다.

    검색어를 개념(제품·세대·브랜드·기기 / 부품·증상 / 주제 / 시장·채널)으로 바꿔
    말뭉치의 개념 주석(제품 링크·결함 부품×증상·주제)과 다국어 동의어로 찾습니다.
    그래서 표기·언어가 달라도 뜻이 같으면 결과가 같습니다 —
      'S25 울트라 발열' = 'Galaxy S25 Ultra overheating' = 's25u 발열 문제'
      '폴드7 힌지 먼지' · '노트9 배터리' · 'S25 시리즈 배터리 광탈'(세대 전 모델) ·
      '레딧에서 픽셀 10 발열'(채널) · '인도 S25 불만'(시장) · 'S25 vs 아이폰 16 배터리'(둘 다 언급한 글)
    모델코드(SM-S938B · sm-s938n · SM-S9380)는 그 제품으로 봅니다 — 'SM-S938B 발열' = 'S25 울트라 발열'.
    출시 전 세대의 코드(SM-S958U)는 모델을 못 정해 그 세대 전부를 범위로 씁니다.
    PLM 과제(SP 허브)가 연결된 서버에서는 과제코드 · 과제명(개발명) · 개발모델명으로도 찾습니다 —
    'TK-001 발열' · '<개발명> 배터리' → 그 과제의 개발모델 → 제품. 연결 여부는 get_ontology 의 plm.
    사전에 없는 표현도 찾습니다 — 은유·완곡 표현은 로컬 LLM 이 증상 개념으로 옮기고
    ('폰이 손난로 같아요' → 발열), 비슷한 문장은 임베딩(bge-m3)으로 찾습니다
    ('S25 울트라 하루를 못 버텨요' → 배터리 광탈 글). 사전이 질의를 다 설명하면 임베딩은 쓰지
    않습니다(표기가 달라도 결과가 같게).
    각 결과의 matched 에 왜 걸렸는지(결함 주석·본문 동의어·의미 유사·대표 제품 …)가 있습니다.
    해석 내용(무엇을 무엇으로 봤는지)은 semantic_search 로 확인하십시오.

    결과의 product_name 이 비어 있으면 본문에 특정 모델명이 없는 글입니다(일반 토론·짧은
    댓글·브랜드만 언급) — 처리 실패가 아닙니다. 현재 태깅률은 get_unmapped_voc 의 coverage.

    반환은 늘 객체입니다 — {total, results, strategy, match_mode, notes}. results 가 비었으면
    notes 에 까닭(시도한 경로·범위·기간)이 있습니다 — 0건을 "VOC 가 없다" 로 단정하지 마십시오.
    strategy 가 'keyword' 면 시맨틱이 꺼졌거나 개념을 못 찾은 것이니 notes 를 함께 전하십시오.
    작성자 이름·계정·메일·전화·IMEI·일련번호 꼴은 가려서 나갑니다(개인정보 기본 가림, 모든 도구 공통).

    Args:
        keyword: 검색어. 자연어 그대로 ('S25 울트라 발열 불만', '최근 폴드 주름')
        product_code: 특정 제품으로 범위 한정 — 선택 (해석된 제품보다 우선)
        limit: 반환 건수 (기본 30, 최대 100)
        days: 최근 N일(발행일 기준) — 선택. 검색어에 '최근'·'요즘' 이 있으면 90일로 본다
        order: 'relevance'(기본, 관련도→최신) | 'recent'(최신순) | 'engagement'(참여도순)
        match: 키워드 검색으로 갈 때의 경로 — 'auto' | 'phrase' | 'fts' | 'substring'
        semantic: False 면 온톨로지 없이 예전 키워드 검색만 씁니다
    """
    if not semantic:
        d = await search_voc_detail(keyword, product_code, min(limit, 100), days,
                                    "recent" if order == "relevance" else order, match)
        return {"total": d["total"], "truncated": d["truncated"], "results": d["results"], "strategy": "keyword",
                "match_mode": d["match_mode"], "notes": d["notes"]}
    r = await semantic_search_tool(keyword, product_code, limit, days, order, match)
    return {"total": r["total"], "results": r["results"], "strategy": r["strategy"],
            "match_mode": r.get("match_mode") or r["strategy"],
            "notes": (r.get("interpretation") or {}).get("notes", [])}


@mcp.tool()
async def semantic_search(
    query: str,
    product_code: Optional[str] = None,
    limit: int = 30,
    days: Optional[int] = None,
    order: str = "relevance",
) -> dict:
    """search_voc 와 같은 시맨틱 검색에 **해석 내용**을 함께 돌려줍니다.

    interpretation —
      concepts  인식한 개념 [{type, key, label, via(근거 표현), role}]
                role: scope(범위) · match(후보) · boost(가산) · derived(관계로 추가)
      scope     실제로 건 제품 범위 (세대 확장·비교 의도면 '모두 언급' 포함)
      expansion 증상·부품별로 본문에서 찾은 다국어 동의어
      notes     판단 근거 ('S25' 는 기본 모델로 봤다 — 전 세대는 'S25 시리즈' 등)
      llm_symptoms  사전에 없는 표현에서 LLM 이 추론한 증상 키 (role='inferred', 사전 증상의 절반으로 친다)
      vector    임베딩 사용 여부·후보 수 (사전이 다 설명하면 쓰지 않는다)
    strategy — semantic(개념) · vector(개념 없이 임베딩·낱말) · keyword(둘 다 없을 때 예전 검색)
    total 은 조건을 만족한 전체 건수, results 는 관련도 순 상위 limit 건(같은 본문은 접는다).

    Args:
        query: 검색어 (자연어)
        product_code: 제품 범위 강제 — 선택
        limit: 반환 건수 (기본 30, 최대 100)
        days: 최근 N일 — 선택
        order: 'relevance' | 'recent' | 'engagement'
    """
    return await semantic_search_tool(query, product_code, limit, days, order)


@mcp.tool()
async def get_ontology(concept: Optional[str] = None) -> dict:
    """VOC 온톨로지(개념 그래프)를 둘러봅니다.

    concept 없이 부르면 전체 구조 — 개념 종류(제품·세대·시리즈·폼팩터·기기·브랜드·
    부품 16·증상 32·주제 13·시장), 증상→함의 부품·심각도·주제 관계, 개념별 본문 검색어,
    말뭉치에서 그 개념으로 주석된 VOC 수.
    concept 를 주면 그 개념 하나 — 'overheat' · 'symptom:burn_in' · 'hinge' · 'battery'(주제) ·
    'GS25U'(제품: 세대·같은 세대 모델·링크된 VOC 수). 키가 아니면 질의로 해석해 보여줍니다.
    전체 구조에는 model_codes(모델코드 사전 크기)와 plm(PLM 과제 거울 — 켜졌나 · 과제·개발모델 수 ·
    마지막으로 받은 시각 · 마지막 받기 결과 last_result ok/check/error 와 확인 필요 사유 problems)이 함께 나옵니다.
    plm.active 가 false 면 과제코드·과제명 검색이 꺼져 있습니다.

    Args:
        concept: 개념 키 또는 자연어 — 선택
    """
    return await get_ontology_tool(concept)


@mcp.tool()
async def resolve_product_keyword(keyword: str) -> dict:
    """제품명/별칭을 **제품 코드로 확정**하고, 키워드 검색과의 차이를 알려줍니다.

    제품을 묻는 질문에서 자유 텍스트 검색은 표기에 따라 결과가 크게 흔들립니다
    (2026-10-15 실측) — 'S25 Ultra' 9,680 · 'Galaxy S25 Ultra' 2,296 ·
    'S25Ultra' 315 · '갤럭시 S25 울트라' 88. 제품 태깅 기준은 GS25U 9,502 입니다.
    이 도구로 코드를 먼저 확정한 뒤 product_code 로 조회하면 흔들리지 않습니다.

    Args:
        keyword: 제품명·별칭·코드 중 아무 것 ('S25 울트라', 'Galaxy Z Fold8', 'GZF8')

    Returns:
        resolved_code, tagged_count, keyword_hits, candidates(최대 5), note
    """
    return await resolve_product_keyword_tool(keyword)


# ── 분석 ──────────────────────────────────────────────────

@mcp.tool()
async def analyze_sentiment_trend(
    product_code: str,
    period_days: int = 90,
    granularity: str = "week",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> dict:
    """
    제품의 감성 점수 시계열 트렌드를 분석합니다.
    start_date/end_date 로 임의 구간을 지정할 수 있고(예: 특정 분기·연도),
    지정하지 않으면 최근 period_days 일을 봅니다.

    Args:
        product_code: 제품 코드
        period_days: 분석 기간 (일, 기본 90) — start/end 미지정 시 적용
        granularity: 집계 단위 (day, week, month — 기본 week)
        start_date: 시작일 YYYY-MM-DD (published_at >=) — 선택
        end_date: 종료일 YYYY-MM-DD (published_at <, 반열림) — 선택
    """
    return await analyze_sentiment_trend_tool(
        product_code, period_days, granularity, start_date, end_date)


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
async def site_health() -> dict:
    """
    플랫폼(사이트)별 수집 현황과 수집 워커 생존을 반환합니다.

    반환 — {stale_hours, worker, summary, platforms}. platforms 는 각 플랫폼의 24h 수집량·부정 건수·
    평균 감성, **전 기간** 마지막 수집 시각, 상태(healthy / stale / no_data_ever)입니다. 정체(stale)는
    alert_check 와 같은 기준(마지막 수집이 12시간 넘음)입니다. worker.status 가 never_ran 이면 이 서버는
    수집하지 않고 덤프로만 받는 서버라 마지막 수집 시각은 원천 서버의 것입니다.
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
    product_code: Optional[str] = None, top_n: int = 15, days: int = 0
) -> dict:
    """국가별 VOC 분포를 가로 막대 차트 규격으로 반환. product_code 생략 시 전체.

    Args:
        product_code: 제품 한정 — 선택
        top_n: 상위 국가 수 (기본 15, 최대 50)
        days: 0 이면 전기간, >0 이면 최근 N일(published_at 기준)
    """
    return await chart_country_distribution_tool(product_code, top_n, days)


@mcp.tool()
async def chart_category_distribution(
    product_code: Optional[str] = None, top_n: int = 15,
    days: int = 0, country: Optional[str] = None,
) -> dict:
    """카테고리별 VOC 분포를 가로 막대 차트 규격으로 반환. product_code 생략 시 전체.

    막대는 이슈 카테고리만입니다. comparison(경쟁사 비교)·model_mention(모델 언급)은 글의 성격 표지라
    meta_tags 로 따로 줍니다 — 모델 이름만 나와도 붙어 '최대 이슈' 로 읽으면 틀립니다.

    Args:
        product_code: 제품 한정 — 선택
        top_n: 상위 카테고리 수 (기본 15, 최대 50)
        days: 0 이면 전기간, >0 이면 최근 N일
        country: 국가코드(예 'US') 지정 시 해당국으로 드릴다운 — 선택
    """
    return await chart_category_distribution_tool(product_code, top_n, days, country)


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


# ── 차원 분해 도구 (보강 2026-06-25) — 미노출 데이터 차원 커버 ──
@mcp.tool()
async def get_platform_breakdown(
    product_code: Optional[str] = None, period_days: int = 30, top_n: int = 20
) -> list:
    """플랫폼(커뮤니티/사이트)별 VOC 건수·평균감성·부정비율을 반환합니다.

    platform_id 는 전 레코드에 채워져 있어 전수 커버됩니다. 어느 채널에서
    말이 많고 감성이 나쁜지 파악할 때 사용합니다.

    Args:
        product_code: 제품 한정 — 선택 (없으면 전체 VOC)
        period_days: 분석 기간 (일, 기본 30). 0 이면 전기간.
        top_n: 상위 플랫폼 수 (기본 20, 최대 100)
    """
    return await get_platform_breakdown_tool(product_code, period_days, top_n)


@mcp.tool()
async def get_engagement_leaders(
    product_code: Optional[str] = None, period_days: int = 30, limit: int = 20
) -> list:
    """engagement_score(좋아요/댓글/공유 기반) 상위 VOC를 반환합니다 — 바이럴·고영향 게시물.

    Args:
        product_code: 제품 한정 — 선택 (없으면 전체)
        period_days: 분석 기간 (일, 기본 30). 0 이면 전기간.
        limit: 반환 건수 (기본 20, 최대 100)
    """
    return await get_engagement_leaders_tool(product_code, period_days, limit)


@mcp.tool()
async def get_language_breakdown(period_days: int = 0, top_n: int = 25) -> list:
    """언어(language_detected)별 VOC 건수·평균감성을 반환합니다 — 글로벌 커버리지 가시화.

    Args:
        period_days: 분석 기간 (일). 기본 0 = 전기간.
        top_n: 상위 언어 수 (기본 25, 최대 60)
    """
    return await get_language_breakdown_tool(period_days, top_n)


# ── 보강 R2 (2026-06-26) — matview 생애주기/지식그래프 + 미태깅 점검 ──
@mcp.tool()
async def get_product_timeline(product_code: str, recent_months: int = 0) -> dict:
    """제품의 월별 VOC 생애주기를 반환합니다 (출시일 기준 건수/감성/부정율 추이).

    galaxy_master_timeline matview 기반. 제품 출시 후 VOC 가 어떻게 변해왔는지 본다.

    Args:
        product_code: 제품 코드 (예 'GS25')
        recent_months: 최근 N개월만 — 0 이면 전체 (기본 0)
    """
    return await get_product_timeline_tool(product_code, recent_months)


@mcp.tool()
async def get_kg_relations(
    node: Optional[str] = None, edge_type: Optional[str] = None, top_n: int = 30
) -> list:
    """지식그래프 관계를 반환합니다 — 제품↔카테고리/국가/플랫폼 연관 (가중치·감성).

    kg_edges_daily matview 기반. 특정 노드의 연관 관계나 특정 관계유형을 가중치 순으로.

    Args:
        node: 'product:GS25' / 'category:battery' / 'country:US' 등 — source/target 매칭 (선택)
        edge_type: product_category / product_country / product_platform 중 — 선택
        top_n: 가중치 합 상위 N (기본 30, 최대 100)
    """
    return await get_kg_relations_tool(node, edge_type, top_n)


@mcp.tool()
async def get_unmapped_voc(reason: Optional[str] = None, limit: int = 20) -> dict:
    """미태깅(제품 미매핑) VOC를 점검합니다 — 현재 태깅률(coverage)과 미태깅분 진단.

    coverage 에 실측 태깅률을, breakdown·samples 에 unmapped_reason 분포와 샘플을 담습니다.
    태깅률을 말해야 할 때는 이 도구의 coverage 를 인용하십시오(설명문에 숫자를 두지 않습니다).
    미태깅의 대부분은 처리 실패가 아니라 본문에 특정 모델명이 없는 글입니다(note 참조).

    Args:
        reason: no_model_mention / non_galaxy / too_short / noise 중 — 샘플 한정 (선택)
        limit: 샘플 건수 (기본 20, 최대 100)
    """
    return await get_unmapped_voc_tool(reason, limit)


# SF_MCP_TOKEN 이 설정되면 Authorization: Bearer 검증을 끼운다(에이전트용 정적 서비스 토큰).
# streamable-http 는 스트리밍이라 BaseHTTPMiddleware(응답 버퍼링) 대신 순수 ASGI 로 헤더만 검사.
# 미설정 시 기존처럼 무인증 — standalone 안전.
class _BearerGate:
    def __init__(self, app, token: str) -> None:
        self.app, self.token = app, token

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != f"Bearer {self.token}":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
                return
        await self.app(scope, receive, send)



# ── 결함 분석 (voc_defects / voc_product_links / v_voc_lifecycle) ──────────
@mcp.tool()
async def defect_profile(
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    severity: Optional[str] = None,
    period_days: int = 90,
    limit: int = 20,
) -> dict:
    """
    제품·부품·증상별 **구조화 결함 프로파일**을 반환합니다.

    단순 키워드 카운트가 아니라 (부품, 증상, 심각도) 삼중항 집계입니다.
    예: 갤럭시 Z 폴드8 → display/crease, hinge/dust_ingress, battery/drain.

    Args:
        product_code: 제품 코드 (예: GZF8, GS26U, AP16P). 생략 시 전체.
        component: 부품 필터 (hinge, display, battery, camera, charging_port,
                   speaker, button, back_glass, frame, fingerprint, software,
                   connectivity, sim, thermal, s_pen)
        severity: safety | non_functional | degraded | cosmetic
        period_days: 조회 기간 (기본 90일)
        limit: 상위 N개

    반환에 effective_platforms(유효 플랫폼 수 = 1/HHI)가 포함됩니다.
    2 미만이면 한 커뮤니티의 반향이라 신뢰도가 낮습니다.
    """
    return await defect_profile_tool(product_code, component, severity,
                                     period_days, limit)


@mcp.tool()
async def defect_anomalies(limit: int = 20, severity: Optional[str] = None) -> dict:
    """
    최근 탐지된 **결함 급등**(제품×부품×증상) 목록을 반환합니다.

    신제품은 이전 세대의 동일 출시후 구간과 비교(baseline_mode=lifecycle)해
    출시 효과를 제거한 값입니다. 단일 커뮤니티 쏠림은 탐지 단계에서 배제됩니다.

    Args:
        limit: 상위 N개
        severity: critical | warning 필터
    """
    return await defect_anomalies_tool(limit, severity)


@mcp.tool()
async def lifecycle_compare(
    product_code: str, max_week: int = 12, defect_only: bool = False,
) -> dict:
    """
    제품을 **직전 세대와 동일 라이프사이클 주차로 비교**합니다.

    신제품은 출시 직후 원래 부정 언급이 급증하므로, 자기 과거가 아니라 이전 세대의
    같은 주차와 비교해야 진짜 이상인지 알 수 있습니다.
    예: GZF8(폴드8) vs GZF7(폴드7) 출시 0~12주차 부정률·결함률.

    Args:
        product_code: 제품 코드 (예: GZF8, GS26U)
        max_week: 비교할 최대 주차 (기본 12)
        defect_only: True 면 결함이 있는 주차만
    """
    return await lifecycle_compare_tool(product_code, max_week, defect_only)


# ── 결함 시나리오 도출 ────────────────────────────────────────────────────
# 결함 "시나리오"는 숫자 하나로 서지 않는다. 아래 도구들은 **같은 필터 축**을 받아
# 한 슬라이스를 여러 관점으로 보게 한다. 권장 조합 —
#   1) defect_profile      무엇이 문제인가 (제품×부품×증상 상위)
#   2) defect_timeline     언제부터인가, 가속 중인가
#   3) defect_breakdown    누가 어디서 말하는가 (플랫폼·국가·양상)
#   4) defect_onset        출시 후 며칠에 터지는가 (초기불량 vs 마모)
#   5) defect_cooccurrence 무엇과 함께 터지는가 (복합 고장 모드)
#   6) defect_evidence     실제로 뭐라고 하는가 (원문 근거)
# 공통 필터: product_code · component · symptom · severity · modality ·
#            brand · category · country · platform
@mcp.tool()
async def defect_timeline(
    interval: str = "week",
    period_days: int = 180,
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    symptom: Optional[str] = None,
    severity: Optional[str] = None,
    modality: Optional[str] = None,
    brand: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """결함 슬라이스의 **시계열** — 언제 시작됐고 가속 중인지.

    단일 카운트로는 '원래 그런 것'과 '새로 터진 것'을 가를 수 없습니다.
    first_seen 과 버킷별 추이를 함께 반환합니다.

    Args:
        interval: day | week | month (기본 week)
        period_days: 조회 기간 (기본 180일)
        나머지: 공통 필터 — 생략 시 전체
    """
    return await defect_timeline_tool(
        interval=interval, period_days=period_days, product_code=product_code,
        component=component, symptom=symptom, severity=severity,
        modality=modality, brand=brand, country=country, platform=platform)


@mcp.tool()
async def defect_breakdown(
    by: str = "platform",
    period_days: int = 90,
    limit: int = 20,
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    symptom: Optional[str] = None,
    severity: Optional[str] = None,
    modality: Optional[str] = None,
    brand: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """결함 슬라이스를 **임의 축으로 분해** — 누가 어디서 말하는가.

    firsthand_pct 를 함께 줍니다. 낮으면 전언·기사 반향이라 실제 고장률이 아닙니다.

    Args:
        by: platform | country | modality | severity | component | symptom |
            product | brand | category
        나머지: 공통 필터
    """
    return await defect_breakdown_tool(
        by=by, period_days=period_days, limit=limit, product_code=product_code,
        component=component, symptom=symptom, severity=severity,
        modality=modality, brand=brand, country=country, platform=platform)


@mcp.tool()
async def defect_cooccurrence(
    period_days: int = 180,
    limit: int = 20,
    min_docs: int = 3,
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    brand: Optional[str] = None,
    country: Optional[str] = None,
) -> dict:
    """같은 글에서 **함께 나타나는 결함 쌍** — 복합 고장 모드 발굴.

    예: battery/drain + battery/overheat, software/lag + thermal/overheat.
    단일 증상 집계로는 보이지 않는 연쇄를 드러냅니다.
    주의 — 동시 등장이지 인과가 아닙니다(한 글이 여러 불만을 나열했을 수 있음).
    """
    return await defect_cooccurrence_tool(
        period_days=period_days, limit=limit, min_docs=min_docs,
        product_code=product_code, component=component, brand=brand, country=country)


@mcp.tool()
async def defect_onset(
    period_days: int = 730,
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    symptom: Optional[str] = None,
    severity: Optional[str] = None,
    brand: Optional[str] = None,
) -> dict:
    """**출시 후 며칠에 보고되는가** — 초기불량과 마모고장을 가릅니다.

    median_days 가 작고 within_30d 비중이 높으면 초기불량,
    after_1y 가 크면 마모·열화 패턴입니다.
    주의 — 수집 시작(2026-05) 이전 출시 제품은 초기 구간이 비어 median 이 왜곡됩니다.
    """
    return await defect_onset_tool(
        period_days=period_days, product_code=product_code, component=component,
        symptom=symptom, severity=severity, brand=brand)


@mcp.tool()
async def defect_evidence(
    period_days: int = 180,
    limit: int = 10,
    modality: Optional[str] = "firsthand",
    product_code: Optional[str] = None,
    component: Optional[str] = None,
    symptom: Optional[str] = None,
    severity: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """결함 슬라이스의 **실제 사용자 발언** — 시나리오를 구체화하는 원문 근거.

    기본은 modality='firsthand'(자기 기기에서 실제로 겪은 증상)입니다.
    전언·기사·구매고민은 고장 서술이 아니므로 기본에서 제외합니다
    (modality=None 으로 전체를 볼 수 있습니다).
    source_url 기준 중복 제거로 같은 글의 페이지 분할을 한 건으로 셉니다.
    """
    return await defect_evidence_tool(
        period_days=period_days, limit=limit, modality=modality,
        product_code=product_code, component=component, symptom=symptom,
        severity=severity, country=country, platform=platform)


# ── 전체 통계 (포털 LLM 이 '감질맛나는' 답을 하지 않도록) ──────────────────
# 문제는 도구가 **행을 주고 통계를 안 준 것**이었다. search_voc 는 30행을 돌려줄 뿐
# "총 몇 건인지, 전기 대비 얼마나 늘었는지, 어느 제품에 몰렸는지"를 말해주지 못한다.
# 아래 4종은 답변의 **전제**를 만들어준다. 전부 1초대에 나온다.
@mcp.tool()
async def corpus_overview(days: Optional[int] = None) -> dict:
    """코퍼스 **전체 현황** — 규모·기간·커버리지·품질·결함률을 한 번에.

    "우리가 뭘 갖고 있나"에 답합니다. 어떤 분석이든 앞에 놓을 전제가 됩니다.

    Args:
        days: 최근 N일로 한정 — 생략 시 전 기간
    """
    return await corpus_overview_tool(days)


@mcp.tool()
async def keyword_stats(
    keyword: str,
    days: int = 90,
    interval: str = "week",
    top_n: int = 8,
    product_code: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """키워드의 **통계 프로파일** — search_voc 의 통계판.

    search_voc 가 개별 글을 준다면 이건 총량·추세·분포·감성을 줍니다.
    "발열 1,012건, 전기 대비 +561%, GZF8 이 32%, 부정 82%" 같은 답을 만들 수 있습니다.
    한국어 키워드도 그대로 넣으면 됩니다(재현율 100%).

    반환: total(건수·고유URL·플랫폼·국가·감성), vs_previous_period(증감),
          series(시계열), top(제품·플랫폼·국가 상위와 비중)
    """
    return await keyword_stats_tool(
        keyword, days=days, interval=interval, top_n=top_n,
        product_code=product_code, brand=brand, category=category,
        country=country, platform=platform)


@mcp.tool()
async def period_compare(
    days: int = 30,
    by: str = "product",
    limit: int = 15,
    keyword: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
) -> dict:
    """**최근 N일 vs 직전 N일** — 무엇이 늘고 줄었나.

    절대 건수만으로는 변화를 볼 수 없습니다. risers/fallers 를 함께 반환합니다.

    Args:
        by: product | brand | category | country | platform
        keyword: 특정 키워드로 한정 — 선택 (한국어 가능)
    """
    return await period_compare_tool(
        days=days, by=by, limit=limit, keyword=keyword,
        brand=brand, category=category, country=country)


@mcp.tool()
async def voc_breakdown(
    by: str = "product",
    days: Optional[int] = 30,
    limit: int = 20,
    keyword: Optional[str] = None,
    product_code: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
    sentiment: Optional[str] = None,
    include_mentions: bool = False,
) -> dict:
    """전체 VOC 를 **임의 축으로 분해** — 결함에 한정되지 않습니다.

    각 행에 share_pct(슬라이스 내 비중)와 negative_pct 가 붙습니다.

    Args:
        by: product | brand | category | country | platform | platform_kind |
            language | sentiment
        keyword: 키워드로 슬라이스 한정 — 선택 (한국어 가능)
        include_mentions: 비교글에서 **언급만 된 제품**까지 셉니다 (기본 False).
            기본값은 대표 제품 기준이라 "S26 vs iPhone 17" 같은 글에서 한쪽만
            잡힙니다. 켜면 양쪽 다 잡히지만 한 글이 제품 수만큼 중복 계상됩니다.
            경쟁 비교·신제품 화제성을 볼 때 켜세요 — 신제품일수록 비교 대상으로만
            등장해 기본 기준에서 빠집니다(실측 GS27 361→1,714건).
    """
    return await voc_breakdown_tool(
        by=by, days=days, limit=limit, keyword=keyword,
        product_code=product_code, brand=brand, category=category,
        country=country, platform=platform, sentiment=sentiment,
        include_mentions=include_mentions)


# ── 서버 기동 ─────────────────────────────────────────────────────────────
# **이 블록은 반드시 파일 맨 아래에 있어야 한다.** uvicorn.run 은 블로킹이라
# 여기서 실행이 멈춘다. 중간에 두면 그 아래 @mcp.tool() 정의가 아예 실행되지
# 않아 도구가 조용히 사라진다 — 실측(2026-09-15): 이 블록이 514줄에 있어서
# 뒤에 정의된 결함 5종·통계 4종이 tools/list 에 나오지 않았다(34개 중 25개만).
# import 로 열면 이 블록을 건너뛰어 34개가 다 보이는 탓에 시험으로는 안 잡혔다.
# test_server_entrypoint_is_last 가 이걸 지킨다.
if __name__ == "__main__":
    _token = os.getenv("SF_MCP_TOKEN", "").strip()
    if _token:
        import uvicorn
        uvicorn.run(_BearerGate(mcp.streamable_http_app(), _token),
                    host=mcp.settings.host, port=mcp.settings.port)
    else:
        mcp.run(transport="streamable-http")
