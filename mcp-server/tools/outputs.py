# MCP 도구 결과 모델 — 반환 타입으로 적어 tools/list 에 실제 칸이 있는 outputSchema 를 낸다(공통 계약 C7)
"""결과 모델은 계약 C7 의 꼴을 따른다 — 추가 칸을 허용하고 모든 칸에 기본값을 둔다.

도구가 칸을 더해도 깨지지 않고(extra="allow"), 빠진 칸은 null 로 나간다. FastMCP 는 반환값을 이 모델로 검증해
structuredContent 를 짓는다 — 칸 타입이 실제 값과 어긋나면 도구 호출이 실패하므로 확실한 타입만 적는다
(날짜 객체는 date · datetime, 이미 글자로 바꾼 날짜는 str, 모르는 꼴은 Any).
큰 글을 내는 도구(search_voc · semantic_search · query_voc · defect_evidence · get_ontology)는 모델을 달지 않는다 —
SDK 가 구조 결과를 글로도 실어 응답이 두 배가 되므로 게이트웨이가 한쪽만 넘길 때까지(포털 PLAN M2) 둔다.
"""
from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def split_total(rows: list) -> tuple:
    """SQL 창 함수로 센 전체 수(total_rows 칸)를 행에서 떼어 (행, 전체 수) — 칸이 없으면 전체 수는 None(계약 C8)."""
    total = rows[0].get("total_rows") if rows else 0
    for r in rows:
        r.pop("total_rows", None)
    return rows, (int(total) if total is not None else None)


class _Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class _Result(_Out):
    """도구 결과의 맨 위 — 행 모델과 달리 받고 버린 인자를 함께 싣는다."""
    ignored_args: Optional[list[str]] = Field(
        None, description="받았지만 이 도구에 없어 쓰지 않은 인자 이름(오타 의심 — 기본값으로 돌았다). 없으면 null")


class _Page(_Result):
    """목록을 자를 수 있는 결과의 공통 칸(계약 C8)."""
    total: Optional[int] = Field(None, description="조건에 맞는 전체 수 — 자른 목록이면 results 보다 크다(모르면 null)")
    truncated: bool = Field(False, description="results 를 한도에서 잘랐는가")


class _Pipe(_Result):
    pipeline_status: Optional[str] = Field(None, description="ok | not_run(결함 추출 · 제품 링크가 이 서버에 없다 — 빈 결과는 '없음' 이 아니라 '측정 안 함')")
    pipeline_note: Optional[str] = None


# ── 운영 · 인사이트 ───────────────────────────────────────────────────────
class _HighNegative(_Out):
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    total: Optional[int] = None
    negative: Optional[int] = None
    neg_ratio: Optional[float] = None


class _Surge(_Out):
    product_code: Optional[str] = None
    product_name: Optional[str] = None
    neg_24h: Optional[int] = None
    neg_prev_24h: Optional[int] = None
    surge_ratio: Optional[float] = Field(None, description="직전 24h 대비 배수 — 직전이 0 이면 null")


class _StalePlatform(_Out):
    platform_code: Optional[str] = None
    platform_name: Optional[str] = None
    last_seen: Optional[str] = None
    hours_since: Optional[float] = None


class AlertCheckOut(_Result):
    thresholds: dict[str, float] = Field(default_factory=dict, description="neg_ratio · min_volume · neg_surge_ratio · stale_hours")
    high_negative_ratio: list[_HighNegative] = Field(default_factory=list)
    high_negative_ratio_total: Optional[int] = None
    negative_surge: list[_Surge] = Field(default_factory=list)
    negative_surge_total: Optional[int] = None
    stale_platforms: list[_StalePlatform] = Field(default_factory=list)
    stale_total: Optional[int] = None
    summary: Optional[str] = None


class _Worker(_Out):
    status: Optional[str] = Field(None, description="alive | stalled | never_ran")
    last_job_started: Optional[str] = None
    hours_since_last_job: Optional[float] = None
    jobs_24h: Optional[int] = None
    failed_24h: Optional[int] = None
    note: Optional[str] = None


class _PlatformHealth(_Out):
    platform_code: Optional[str] = None
    platform_name: Optional[str] = None
    region: Optional[str] = None
    is_active: Optional[bool] = None
    voc_24h: Optional[int] = None
    negative_24h: Optional[int] = None
    avg_sentiment: Optional[float] = None
    last_collected: Optional[str] = None
    hours_since_last: Optional[float] = None
    status: Optional[str] = Field(None, description="healthy | stale | no_data_ever")


class SiteHealthOut(_Result):
    stale_hours: Optional[float] = None
    worker: Optional[_Worker] = None
    summary: Optional[str] = None
    platforms: list[_PlatformHealth] = Field(default_factory=list)


class _KeywordCount(_Out):
    keyword: Optional[str] = None
    count: Optional[int] = None


class EmergingKeywordsOut(_Result):
    period_days: Optional[int] = None
    product_code: Optional[str] = None
    sampled_records: Optional[int] = None
    sample_capped: Optional[bool] = Field(None, description="표본이 row_limit 에 닿았다 — 기간 전체가 아니라 최근 쪽만 본 것")
    row_limit: Optional[int] = None
    distinct_korean: Optional[int] = None
    distinct_english: Optional[int] = None
    truncated: Optional[bool] = None
    top_korean: list[_KeywordCount] = Field(default_factory=list)
    top_english: list[_KeywordCount] = Field(default_factory=list)


class ChartOut(_Result):
    chart_type: Optional[str] = Field(None, description="line | bar | graph …")
    raw: Any = Field(None, description="차트의 원자료 — 도구마다 꼴이 다르다(설명 참조)")
    echarts_option: dict[str, Any] = Field(default_factory=dict, description="ECharts option — 그대로 렌더한다")
    summary: Optional[str] = None
    total: Optional[int] = None
    truncated: Optional[bool] = None


class _ChangeEvent(_Out):
    id: Optional[int] = Field(None, description="이벤트 id — 다음 호출의 since 로 쓰는 커서")
    event: Optional[str] = Field(None, description="signalforge.<대상>.<일>")
    rule: Optional[str] = None
    severity: Optional[str] = Field(None, description="critical | warning | info")
    at: Optional[datetime] = Field(None, description="일이 난 시각(원천 서버 시각 — 덤프로 받는 서버는 id 순서와 다를 수 있다)")
    value: Optional[float] = None
    threshold: Optional[float] = None
    summary: Optional[str] = None
    detail: Optional[dict[str, Any]] = Field(None, description="탐지기가 남긴 원자료(규칙마다 칸이 다르다)")


class ChangesOut(_Page):
    events: list[_ChangeEvent] = Field(default_factory=list)
    next_cursor: Optional[int] = Field(None, description="다음 호출의 since — 이벤트가 없으면 받은 since 그대로")


# ── 조회 · 분석 ───────────────────────────────────────────────────────────
class _TopIssue(_Out):
    category: Optional[str] = None
    total_count: Optional[int] = None
    negative_rate: Optional[float] = Field(None, description="부정 비율(%, 0 ~ 100)")


class TopIssuesOut(_Page):
    product_code: Optional[str] = None
    period_days: Optional[int] = None
    results: list[_TopIssue] = Field(default_factory=list)


class _CountryRow(_Out):
    country_code: Optional[str] = None
    voc_count: Optional[int] = None
    avg_score: Optional[float] = Field(None, description="평균 감성(-1 ~ 1)")
    positive_rate: Optional[float] = Field(None, description="긍정 비율(%, 0 ~ 100)")


class CountryBreakdownOut(_Page):
    product_code: Optional[str] = None
    period_days: Optional[int] = None
    results: list[_CountryRow] = Field(default_factory=list)


class _Candidate(_Out):
    code: Optional[str] = None
    name_en: Optional[str] = None
    name_ko: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    tagged_count: Optional[int] = None
    exact: Optional[bool] = None


class ResolveOut(_Result):
    keyword: Optional[str] = None
    match_mode: Optional[str] = None
    keyword_hits: Optional[int] = Field(None, description="낱말 검색 건수 — 시간 상한을 넘기면 어림(keyword_hits_exact=false) 또는 null")
    keyword_hits_exact: Optional[bool] = None
    candidates: list[_Candidate] = Field(default_factory=list, description="후보 제품(최대 5)")
    resolved_code: Optional[str] = None
    resolved_via: Optional[str] = Field(None, description="catalog_name | alias | plm")
    resolved_codes: Optional[list[str]] = Field(None, description="PLM 으로 푼 제품 전부(프로젝트 하나 → 제품 여럿)")
    tagged_count: Optional[int] = None
    status: Optional[str] = Field(None, description="resolved | no_catalog_match | no_tagged_records")
    note: Optional[str] = None
    core_key: Optional[dict[str, Any]] = Field(None, description="{type_slug, key} — 코어 객체가 없으면 null")
    matched_by: Optional[str] = None
    via: Any = None
    resolutions: Any = None
    reason: Optional[str] = None
    core_unresolved: Any = None
    plm_caution: Optional[dict[str, Any]] = None


class _TrendPoint(_Out):
    period: Optional[date] = None
    positive: Optional[int] = None
    negative: Optional[int] = None
    neutral: Optional[int] = None
    avg_score: Optional[float] = None


class _TrendRange(_Out):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    period_days: Optional[int] = None


class SentimentTrendOut(_Result):
    product_code: Optional[str] = None
    granularity: Optional[str] = None
    range: Optional[_TrendRange] = None
    data: list[_TrendPoint] = Field(default_factory=list)


# ── 차원 분해 · 커버리지 ──────────────────────────────────────────────────
class _PlatformRow(_Out):
    platform_code: Optional[str] = Field(None, description="플랫폼 코드 — 다른 도구의 platform 인자에 그대로 쓴다")
    platform: Optional[str] = Field(None, description="표시 이름")
    voc_count: Optional[int] = None
    avg_score: Optional[float] = None
    negative_rate: Optional[float] = Field(None, description="부정 비율(%, 0 ~ 100)")


class PlatformBreakdownOut(_Page):
    results: list[_PlatformRow] = Field(default_factory=list)


class _EngagementRow(_Out):
    id: Optional[int] = None
    source_url: Optional[str] = None
    author_name: Optional[str] = None
    platform_name: Optional[str] = None
    country_code: Optional[str] = None
    sentiment_label: Optional[str] = None
    likes_count: Optional[int] = None
    comments_count: Optional[int] = None
    shares_count: Optional[int] = None
    engagement_score: Optional[float] = Field(None, description="참여도 점수(0 ~ 100, 로그 눈금)")
    snippet: Optional[str] = None
    published_at: Optional[datetime] = None


class EngagementLeadersOut(_Page):
    results: list[_EngagementRow] = Field(default_factory=list)


class _LanguageRow(_Out):
    language: Optional[str] = None
    voc_count: Optional[int] = None
    avg_score: Optional[float] = None


class LanguageBreakdownOut(_Page):
    results: list[_LanguageRow] = Field(default_factory=list)


class _Month(_Out):
    month: Optional[str] = None
    voc_count: Optional[int] = None
    sent_avg: Optional[float] = None
    neg_rate: Optional[float] = Field(None, description="부정 비율(0 ~ 1)")


class ProductTimelineOut(_Result):
    product_code: Optional[str] = None
    name_ko: Optional[str] = None
    series: Optional[str] = None
    released_at: Optional[str] = None
    months: Optional[int] = None
    timeline: list[_Month] = Field(default_factory=list)
    status: Optional[str] = None
    note: Optional[str] = None


class _KgEdge(_Out):
    edge_type: Optional[str] = None
    source: Optional[str] = None
    target: Optional[str] = None
    weight: Optional[float] = Field(None, description="잇는 VOC 수(일별 합)")
    sent_avg: Optional[float] = None


class KgRelationsOut(_Page):
    results: list[_KgEdge] = Field(default_factory=list)
    status: Optional[str] = Field(None, description="aggregating 이면 미리 계산하는 뷰가 아직 비었다(note 참조)")
    note: Optional[str] = None


class _TagCoverage(_Out):
    active_total: Optional[int] = None
    tagged: Optional[int] = None
    untagged: Optional[int] = None
    tagged_pct: Optional[float] = None


class _ReasonCount(_Out):
    reason: Optional[str] = None
    count: Optional[int] = None


class _UnmappedSample(_Out):
    id: Optional[int] = None
    platform_name: Optional[str] = None
    country_code: Optional[str] = None
    unmapped_reason: Optional[str] = None
    sentiment_label: Optional[str] = None
    snippet: Optional[str] = None


class UnmappedVocOut(_Result):
    coverage: Optional[_TagCoverage] = None
    untagged_total: Optional[int] = None
    breakdown: list[_ReasonCount] = Field(default_factory=list)
    samples: list[_UnmappedSample] = Field(default_factory=list)
    note: Optional[str] = None


# ── 전체 통계 ─────────────────────────────────────────────────────────────
class _Scale(_Out):
    voc_total: Optional[int] = None
    oldest: Optional[str] = Field(None, description="1퍼센타일 발행일(파싱 이상치 방어)")
    oldest_raw: Optional[str] = None
    newest: Optional[str] = None
    platforms: Optional[int] = None
    countries: Optional[int] = None
    languages: Optional[int] = None


class _OverviewCoverage(_Out):
    tagged: Optional[int] = None
    tagged_pct: Optional[float] = None
    products_seen: Optional[int] = None
    catalog_products: Optional[int] = None
    catalog_active: Optional[int] = None
    catalog_by_category: dict[str, int] = Field(default_factory=dict)


class _Quality(_Out):
    categorized_pct: Optional[float] = None
    negative_pct: Optional[float] = None


class _OverviewDefects(_Out):
    pipeline_status: Optional[str] = None
    docs: Optional[int] = None
    records: Optional[int] = None
    firsthand: Optional[int] = None
    defect_rate_pct: Optional[float] = Field(None, description="결함 글 비율(%) — 결함 추출이 안 돈 서버는 null")
    pipeline_note: Optional[str] = None


class _Workflow(_Out):
    purpose: Optional[str] = None
    steps: list[str] = Field(default_factory=list, description="부르는 차례(도구 이름)")
    entry: Optional[str] = Field(None, description="이 업무의 입구 도구")


class _Guide(_Out):
    workflows: list[_Workflow] = Field(default_factory=list)
    rules: list[str] = Field(default_factory=list)


class CorpusOverviewOut(_Result):
    period_days: Optional[int] = None
    scale: Optional[_Scale] = None
    coverage: Optional[_OverviewCoverage] = None
    quality: Optional[_Quality] = None
    defects: Optional[_OverviewDefects] = None
    note: Optional[str] = None
    guide: Optional[_Guide] = Field(None, description="대표 업무와 차례 — 다음에 부를 도구")


class _KeywordTotal(_Out):
    docs: Optional[int] = None
    uniq_urls: Optional[int] = None
    platforms: Optional[int] = None
    countries: Optional[int] = None
    negative: Optional[int] = None
    negative_pct: Optional[float] = None
    avg_sentiment: Optional[float] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None


class _VsPrevious(_Out):
    prev_docs: Optional[int] = None
    delta_pct: Optional[float] = Field(None, description="직전 기간 대비 증감(%) — 직전이 0 이면 null")


class _Bucket(_Out):
    bucket: Optional[str] = None
    docs: Optional[int] = None


class _Share(_Out):
    key: Optional[str] = None
    docs: Optional[int] = None
    share_pct: Optional[float] = None


class _KeywordTops(_Out):
    product: list[_Share] = Field(default_factory=list)
    platform: list[_Share] = Field(default_factory=list)
    country: list[_Share] = Field(default_factory=list)


class KeywordStatsOut(_Result):
    keyword: Optional[str] = None
    days: Optional[int] = None
    match: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    total: Optional[_KeywordTotal] = None
    vs_previous_period: Optional[_VsPrevious] = None
    series: list[_Bucket] = Field(default_factory=list)
    top: Optional[_KeywordTops] = None
    note: Optional[str] = None


class _Delta(_Out):
    key: Optional[str] = None
    current: Optional[int] = None
    previous: Optional[int] = None
    delta: Optional[int] = None
    delta_pct: Optional[float] = None


class PeriodCompareOut(_Result):
    by: Optional[str] = None
    days: Optional[int] = None
    keyword: Optional[str] = None
    match: Optional[str] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    rows: list[_Delta] = Field(default_factory=list)
    groups: Optional[int] = Field(None, description="축 값의 전체 수 — rows 보다 크면 잘린 것")
    truncated: Optional[bool] = None
    risers: list[_Delta] = Field(default_factory=list)
    fallers: list[_Delta] = Field(default_factory=list)
    note: Optional[str] = None


class _BreakdownRow(_Out):
    key: Optional[str] = None
    docs: Optional[int] = None
    negative: Optional[int] = None
    negative_pct: Optional[float] = None
    avg_sentiment: Optional[float] = None
    share_pct: Optional[float] = None


class VocBreakdownOut(_Result):
    by: Optional[str] = None
    days: Optional[int] = None
    keyword: Optional[str] = None
    match: Optional[str] = None
    include_mentions: Optional[bool] = None
    total: Optional[int] = Field(None, description="슬라이스 전체 글 수(include_mentions 면 링크 수)")
    groups: Optional[int] = Field(None, description="축 값의 전체 수 — rows 보다 크면 잘린 것")
    truncated: Optional[bool] = None
    rows: list[_BreakdownRow] = Field(default_factory=list)
    note: Optional[str] = None


# ── 결함 ──────────────────────────────────────────────────────────────────
class _DefectRow(_Out):
    product_code: Optional[str] = None
    component: Optional[str] = None
    symptom: Optional[str] = None
    severity: Optional[str] = None
    count: Optional[int] = Field(None, description="글 수")
    platforms: Optional[int] = None
    effective_platforms: Optional[float] = Field(None, description="1/HHI — 글이 플랫폼에 고루 퍼진 정도")


class DefectProfileOut(_Pipe, _Page):
    period_days: Optional[int] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    defects: list[_DefectRow] = Field(default_factory=list)
    note: Optional[str] = None


class _Anomaly(_Out):
    id: Optional[int] = None
    fired_at: Optional[datetime] = None
    severity: Optional[str] = Field(None, description="경보 심각도 — critical(safety · non_functional 결함) | warning")
    product_code: Optional[str] = None
    component: Optional[str] = None
    symptom: Optional[str] = None
    defect_severity: Optional[str] = None
    recent_count: Optional[int] = None
    ratio: Optional[float] = None
    z: Optional[float] = None
    indep_sources: Optional[int] = Field(None, description="독립 출처(플랫폼) 수")
    effective_platforms: Optional[float] = Field(None, description="옛 탐지기의 1/HHI — 새 이벤트는 null")
    baseline_mode: Optional[str] = Field(None, description="lifecycle(직전 세대 같은 주차) | history(자기 과거)")
    reason: Optional[str] = None


class DefectAnomaliesOut(_Pipe, _Page):
    anomalies: list[_Anomaly] = Field(default_factory=list)
    note: Optional[str] = None


class _LifecycleWeek(_Out):
    product_code: Optional[str] = None
    predecessor_code: Optional[str] = None
    series: Optional[str] = Field(None, description="이 행이 어느 제품의 주차인가")
    week: Optional[int] = None
    total: Optional[int] = None
    negative: Optional[int] = None
    with_defect: Optional[int] = None
    negative_pct: Optional[float] = None
    defect_pct: Optional[float] = None


class LifecycleCompareOut(_Pipe):
    product_code: Optional[str] = None
    predecessor_code: Optional[str] = None
    weeks: list[_LifecycleWeek] = Field(default_factory=list)
    note: Optional[str] = None


class _DefectBucket(_Out):
    bucket: Optional[date] = None
    docs: Optional[int] = None
    uniq_urls: Optional[int] = None
    platforms: Optional[int] = None
    firsthand: Optional[int] = None


class DefectTimelineOut(_Pipe):
    interval: Optional[str] = None
    period_days: Optional[int] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    first_seen: Optional[str] = None
    series: list[_DefectBucket] = Field(default_factory=list)
    note: Optional[str] = None


class _DefectAxisRow(_Out):
    key: Optional[str] = None
    docs: Optional[int] = None
    firsthand: Optional[int] = None
    firsthand_pct: Optional[float] = None


class DefectBreakdownOut(_Pipe, _Page):
    by: Optional[str] = None
    period_days: Optional[int] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    rows: list[_DefectAxisRow] = Field(default_factory=list)


class _DefectPair(_Out):
    defect_a: Optional[str] = None
    defect_b: Optional[str] = None
    co_docs: Optional[int] = None


class DefectCooccurrenceOut(_Pipe, _Page):
    period_days: Optional[int] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    pairs: list[_DefectPair] = Field(default_factory=list)
    note: Optional[str] = None


class DefectOnsetOut(_Pipe):
    docs: Optional[int] = None
    avg_days: Optional[float] = None
    p25_days: Optional[int] = None
    median_days: Optional[int] = None
    p75_days: Optional[int] = None
    within_30d: Optional[int] = None
    after_1y: Optional[int] = None
    period_days: Optional[int] = None
    filters: dict[str, Any] = Field(default_factory=dict)
    note: Optional[str] = None
