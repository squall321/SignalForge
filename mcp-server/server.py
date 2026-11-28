"""
SignalForge MCP Server
FastMCP 기반 — Claude가 VOC 데이터베이스와 직접 대화
"""
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field
from typing import Annotated, Literal, Optional
import contextvars
import functools
import inspect
import logging
import os, asyncio
import re

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
                                   order="relevance", match="auto", strict=False, core_type=None, core_key=None):
        if core_type or core_key:                     # 코어 key 범위는 온톨로지가 건다 — 키워드로 넓히지 않는다(SFO-5)
            return {"query": query, "strategy": "keyword", "match_mode": "none", "total": 0, "results": [],
                    "reason": "semantic_unavailable", "interpretation": {"notes": [_SEM_NOTE]}}
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
from tools.changes import EVENT_NAMES, RULE_EVENT, list_changes_tool
from tools.outputs import (
    AlertCheckOut, ChangesOut, ChartOut, CorpusOverviewOut, CountryBreakdownOut, DefectAnomaliesOut,
    DefectBreakdownOut, DefectCooccurrenceOut, DefectOnsetOut, DefectProfileOut, DefectTimelineOut,
    EmergingKeywordsOut, EngagementLeadersOut, KeywordStatsOut, KgRelationsOut, LanguageBreakdownOut,
    LifecycleCompareOut, PeriodCompareOut, PlatformBreakdownOut, ProductTimelineOut, ResolveOut,
    SentimentTrendOut, SiteHealthOut, TopIssuesOut, UnmappedVocOut, VocBreakdownOut, split_total,
)
from tools.pii import masked


# MCP 공통 계약(포털 docs/mcp-modernize/contract.md) — 인자 설명은 산문 「Args:」 가 아니라 스키마에(C2), 모든 도구에 읽기 ·
# 쓰기 표지(C6). 도구마다 고치지 않고 등록 길목에서 한 번에 옮긴다. Args 절 — 'Args:' 줄 아래 '<이름>: 설명', 더 들여 쓴 줄은
# 앞 인자의 이어지는 설명, 들여 쓰지 않은 줄(Returns: 등)에서 끝난다.
_ARGS_HEAD = re.compile(r"^Args:\s*$")
_ARG_LINE = re.compile(r"^(\s+)([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*:\s*(.*)$")
# 도구는 모두 DB 조회뿐이다(INSERT · UPDATE · DELETE 없음). 쓰는 도구가 생기면 그 도구의 @mcp.tool(annotations=…) 에 따로 단다.
# SDK 1.x 는 camelCase 칸 이름이다 — read_only_hint= 로 쓰면 모르는 칸으로 나가 클라이언트가 표지를 못 받는다
_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
# 닫힌 어휘(계약 C3) — 정해진 낱말 집합은 enum 으로 낸다. 부품 · 증상 · 주제는 온톨로지가 정본이라 거기서 짓는다(못 읽으면 글자).
# 플랫폼 · 국가 · 브랜드는 열린 집합이라 글자 그대로 두고 설명에 찾는 도구를 적는다.
Severity = Literal["safety", "non_functional", "degraded", "cosmetic"]
AlertSeverity = Literal["critical", "warning"]
Modality = Literal["firsthand", "worry", "question", "hearsay", "review", "other"]
Interval = Literal["day", "week", "month"]
Sentiment = Literal["positive", "negative", "neutral"]
DeviceCategory = Literal["phone", "watch", "tablet", "buds", "laptop", "band", "headphone", "ring", "xr", "glasses"]
SearchOrder = Literal["relevance", "recent", "engagement"]
MatchMode = Literal["auto", "phrase", "fts", "substring"]
CoreType = Literal["plm_project", "plm_task", "plm_model"]
CrisisCase = Literal["GN7", "GZF1", "GS22U", "GZFL3", "GS20"]
KgEdgeType = Literal["product_category", "product_country", "product_platform"]
UnmappedReason = Literal["no_model_mention", "non_galaxy", "too_short", "noise"]
VocAxis = Literal["product", "brand", "category", "country", "platform", "platform_kind", "language", "sentiment"]
CompareAxis = Literal["product", "brand", "category", "country", "platform"]
DefectAxis = Literal["platform", "country", "modality", "severity", "component", "symptom", "product", "brand",
                     "category"]
EventName = Literal[tuple(EVENT_NAMES.values()) + (RULE_EVENT,)]
# VOC 주제 — 수집기 분류기(crawler/nlp/categorizer.py)의 열세 주제 + 성격 표지(model_mention · others · non_target)
_TOPIC_MARKS = ("model_mention", "others", "non_target")
try:
    import ontology as _O
    Component = Literal[tuple(sorted(_O.COMPONENT_LABELS))]
    Symptom = Literal[tuple(sorted(_O.SYMPTOM_LABELS))]
    Topic = Literal[tuple(sorted(set(_O.TOPIC_LABELS) | set(_TOPIC_MARKS)))]
except Exception:  # noqa: BLE001 — 시맨틱이 꺼진 서버(온톨로지 import 실패)도 뜬다
    Component = Symptom = Topic = str
# 숫자 경계(계약 C4) — 함수 안에서 조용히 자르지 않고 스키마가 막는다. 상한은 인자 설명에도 적는다
IsoDate = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}")]
AnyDays = Annotated[int, Field(ge=0, le=3650)]          # 0 = 전 기간
Days = Annotated[int, Field(ge=1, le=3650)]

# 공통 필터 — 결함 · 통계 도구가 같은 뜻으로 받는다(tools/defects._FILTER_SPECS · shared/stats_sql._SLICE_SPECS). docstring 에
# 설명이 없는 인자는 이 설명을 쓴다(Args 에 적지 않은 공통 필터)
_COMMON_ARGS = {
    "product_code": "제품 코드(예: GZF8 · GS26U · AP16P) — 생략하면 전체. 코드는 resolve_product_keyword 로 찾는다",
    "component": "부품 key(get_ontology 의 components — device 는 부품을 못 정한 기기 전반) — 생략하면 전체",
    "symptom": "증상 key(get_ontology 의 symptoms, 예: overheat · drain · crease · burn_in) — 생략하면 전체",
    "severity": "결함 심각도 — safety(안전) | non_functional(기능 상실) | degraded(성능 저하) | cosmetic(외관) (생략하면 전체)",
    "modality": "글의 양상 — firsthand(본인이 실제 겪은 고장) | worry | question | hearsay | review | other (생략하면 전체)",
    "brand": "제품 브랜드(소문자, 예: samsung · apple · google · xiaomi — 목록은 voc_breakdown(by=\"brand\")) — 생략하면 전체",
    "category": "제품의 기기 종류 — phone · watch · tablet · buds · laptop · band · headphone · ring · xr · glasses (생략하면 전체)",
    "country": "글의 나라 코드(ISO 두 글자 대문자, 예: KR · US · IN) — 생략하면 전체",
    "platform": ("수집 플랫폼 코드(예: reddit · youtube · gsmarena — 목록은 voc_breakdown(by=\"platform\") 의 key 나 "
                 "get_platform_breakdown 의 platform_code) — 생략하면 전체"),
    "sentiment": "감성 — positive | negative | neutral (생략하면 전체)",
}


def _split_args(doc: Optional[str]) -> tuple[str, dict[str, str]]:
    """docstring → (Args 절을 뺀 설명, {인자: 설명})."""
    out, args, cur, base, in_args = [], {}, None, None, False
    for line in inspect.cleandoc(doc or "").splitlines():
        if not in_args and _ARGS_HEAD.match(line):
            in_args = True
            continue
        if in_args:
            if line.strip() and not line[:1].isspace():
                in_args = False
            else:
                m = _ARG_LINE.match(line)
                indent = len(line) - len(line.lstrip())
                if m and (base is None or len(m.group(1)) <= base):
                    base, cur = len(m.group(1)), m.group(2)
                    args[cur] = m.group(3).strip()
                elif line.strip() and base is not None and indent <= base:
                    cur = None               # 인자 꼴이 아닌 같은 들여쓰기 줄('나머지: 공통 필터') — 앞 인자에 붙이지 않는다
                elif cur and line.strip():
                    args[cur] = f"{args[cur]} {line.strip()}".strip()
                continue
        out.append(line)
    return "\n".join(out).strip(), args


def _with_arg_docs(fn) -> tuple[str, inspect.Signature]:
    """도구 함수 → (Args 절을 뺀 설명, 인자마다 Field(description=…) 를 붙인 서명). 타입 · 기본값은 그대로다."""
    desc, docs = _split_args(fn.__doc__)
    sig = inspect.signature(fn)       # docstring 에 없는 공통 필터는 _COMMON_ARGS 로
    docs = {**{k: v for k, v in _COMMON_ARGS.items() if k in sig.parameters}, **docs}
    params = [p.replace(annotation=Annotated[p.annotation, Field(description=docs[p.name])])
              if p.name in docs and p.annotation is not inspect.Parameter.empty else p
              for p in sig.parameters.values()]
    return desc, sig.replace(parameters=params)


# 받고 버린 인자 — SDK 1.x 는 도구에 없는 인자를 오류 없이 버려, 오타(window_days ↔ period_days)가 기본값으로 조용히 돈다.
# 거절하지 않고 드러낸다(포털 71 11-28 — 거절은 부르는 쪽이 이 칸을 보고 고친 뒤에 다시 정한다). call_tool 길목에서 원래 인자로
# 이름을 잡아 같은 태스크의 도구 결과에 싣는다 — 객체는 ignored_args 칸, 글은 끝에 한 줄. 로그에는 이름만 남긴다(값은 검색어 등)
_log = logging.getLogger("signalforge.mcp")
_IGNORED: contextvars.ContextVar = contextvars.ContextVar("sf_ignored_args", default=())
# 열쇠가 동적인 결과(제품 코드 → …)에는 칸을 섞지 않는다 — 소비처가 열쇠를 제품으로 돈다. 로그에만 남긴다
_NO_IGNORED_KEY = {"compare_products"}


def _with_ignored(run, keyed: bool = True):
    @functools.wraps(run)
    async def wrapper(*args, **kwargs):
        out = await run(*args, **kwargs)
        ignored = list(_IGNORED.get())
        if not ignored:
            return out
        if isinstance(out, dict) and keyed:
            return {**out, "ignored_args": ignored}
        if isinstance(out, str):
            return f"{out}\n\n(이 도구에 없는 인자라 쓰지 않았다: {', '.join(ignored)})"
        return out
    return wrapper


class _MaskedFastMCP(FastMCP):
    """나가는 응답은 개인정보를 기본으로 가린다(SG-18 (가)) — 도구마다 잊지 않게 등록 길목에서 감싼다.
    원문(작성자·연락처·계정)은 SignalForge 화면에서 본다. 가리는 범위는 tools/pii.py.
    같은 길목에서 docstring 의 Args 절을 인자 스키마 설명으로 옮기고 기본 표지(읽기 전용)를 단다 — 계약 C2 · C6.
    받고 버린 인자는 결과의 ignored_args 와 경고 로그로 드러낸다(_with_ignored · call_tool)."""

    def tool(self, name=None, title=None, description=None, annotations=None, **kwargs):
        register = super().tool

        def deco(fn):
            desc, sig = _with_arg_docs(fn)
            run = _with_ignored(masked(fn), keyed=fn.__name__ not in _NO_IGNORED_KEY)
            run.__signature__ = sig          # FastMCP 는 인자 스키마를 서명에서 짓는다 — 설명을 붙인 서명을 쓴다
            return register(name=name, title=title, description=description or desc,
                            annotations=annotations or _READ_ONLY, **kwargs)(run)
        return deco

    async def call_tool(self, name, arguments):
        tool = self._tool_manager.get_tool(name)
        known = set(((tool.parameters or {}).get("properties") or {}) if tool else ())
        ignored = tuple(sorted(set(arguments or {}) - known)) if tool else ()
        if ignored:
            _log.warning("[args] %s — 이 도구에 없는 인자를 버렸다: %s (받는 인자: %s)",
                         name, ", ".join(ignored), ", ".join(sorted(known)) or "없음")
        token = _IGNORED.set(ignored)
        try:
            return await super().call_tool(name, arguments)
        finally:
            _IGNORED.reset(token)


def _listed(rows: list, limit: int) -> dict:
    """목록을 돌려주는 도구의 정직한 한도(계약 C8) — {total, truncated, results}.

    집계 도구는 SQL 창 함수로 센 전체 수(total_rows)를 행에 싣는다. 행 도구는 한 건 더 읽어(limit + 1) 잘렸는지만 안다 —
    그때 total 은 다 실었을 때만 준다(잘렸으면 null)."""
    rows, total = split_total(rows)
    results = rows[:limit]
    if total is None:
        truncated = len(rows) > limit
        total = None if truncated else len(results)
    else:
        truncated = total > len(results)
    return {"total": total, "truncated": truncated, "results": results}


# 앱 입구(계약 R3) — corpus_overview 가 말뭉치 개요와 함께 대표 업무의 차례를 준다(계약 R2 — 같은 글이
# docs/mcp-modernize-2026-11-28/workflows.md 에 있다). 도구 이름만 적는다 — 인자는 각 도구 설명에 있다
_GUIDE = {
    "workflows": [
        {"purpose": "한 제품의 결함 양상과 근거", "entry": "resolve_product_keyword",
         "steps": ["resolve_product_keyword", "defect_profile", "defect_timeline", "defect_breakdown",
                   "defect_evidence"]},
        {"purpose": "증상 · 주제로 VOC 찾기와 규모", "entry": "search_voc",
         "steps": ["search_voc", "keyword_stats", "voc_breakdown"]},
        {"purpose": "급등 · 위기 감시", "entry": "list_changes",
         "steps": ["list_changes", "defect_anomalies", "lifecycle_compare", "alert_check", "period_compare"]},
        {"purpose": "경쟁 제품 비교", "entry": "resolve_product_keyword",
         "steps": ["resolve_product_keyword", "compare_products", "voc_breakdown", "period_compare"]},
        {"purpose": "PLM 프로젝트 · 과제 · 개발모델로 VOC 찾기", "entry": "get_ontology",
         "steps": ["get_ontology", "resolve_product_keyword", "search_voc"]},
    ],
    "rules": [
        "제품 코드는 짐작하지 않는다 — resolve_product_keyword 로 확정해 product_code 로 넘긴다",
        "0건은 'VOC 없음' 이 아니다 — 결과의 reason · notes · status · pipeline_status 가 까닭을 준다",
        "잘린 목록은 truncated 로 안다 — 전체 수를 말할 때는 total · groups 를 인용한다",
        "category 는 도구마다 뜻이 다르다 — query_voc · compare_products 는 VOC 주제, 통계 · 결함 도구는 기기 종류",
        "결과에 ignored_args 가 있으면 그 도구에 없는 인자를 넘긴 것이다(쓰지 않고 기본값으로 돌았다) — 인자 이름을 고쳐 다시 부른다",
    ],
}


# @lat: mcp — [[mcp-server]] 참조. 도구 정의.
mcp = _MaskedFastMCP(
    name="SignalForge VOC Intelligence",
    instructions=("Samsung Galaxy 제품군과 경쟁 제품의 VOC 데이터베이스에 질문하는 MCP 서버 — 처음에는 corpus_overview 를 "
                  "부른다(말뭉치 개요와 업무별로 다음에 부를 도구)"),
    port=int(os.getenv("MCP_PORT", "8001")),
    stateless_http=True,                 # 세션을 쥐지 않는다(계약 P1 · 1단계) — 도구는 DB 조회뿐이라 세션 상태가 없다
)


# ── VOC 조회 ───────────────────────────────────────────────

@mcp.tool()
async def query_voc(
    product_code: Optional[str] = None,
    country: Optional[str] = None,
    category: Optional[Topic] = None,
    sentiment: Optional[Sentiment] = None,
    platform: Optional[str] = None,
    keyword: Optional[str] = None,
    start_date: Optional[IsoDate] = None,
    end_date: Optional[IsoDate] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    cursor: Optional[Annotated[str, Field(pattern=r"^\d{1,6}$")]] = None,
) -> dict:
    """조건(제품 · 나라 · 주제 · 감성 · 플랫폼 · 낱말 · 발행일 구간)을 조합해 VOC 원문 행을 발행일 최신순으로 돌려줍니다.

    필터가 정해진 원문 목록이 필요할 때 씁니다. 뜻으로 찾으려면(표기 · 언어가 달라도) search_voc, 건수 · 비중 · 추세는
    keyword_stats · voc_breakdown, 결함 부품 × 증상의 1인칭 원문은 defect_evidence, 많이 퍼진 글은 get_engagement_leaders
    를 씁니다. keyword 는 글자 그대로 맞춥니다(한국어는 원문 부분일치, 영어는 번역문 전문검색) — 동의어로 넓히지 않습니다.
    product_code 를 생략하면 제품이 안 붙은 글까지 봅니다. 행의 product_code 가 비면 본문에 특정 모델명이 없는 글입니다
    (처리 실패가 아님).

    돌려주는 꼴 — {total, truncated, next_cursor, results}. 행은 본문 원문 · 번역문 전체와 감성 · 주제 · 참여도 · 플랫폼 ·
    제품입니다. 한 쪽은 최대 100행이고, 더 있으면 truncated 가 참이며 next_cursor 를 cursor 로 넘기면 다음 쪽입니다.
    total 은 마지막 쪽에서만 셉니다(잘린 쪽은 null — 전체 건수는 keyword_stats). 작성자 · 계정 · 연락처 꼴은 가려서 나갑니다.

    Args:
        product_code: 제품 코드(예: GS25U · GZF7) — 생략하면 제품이 안 붙은 글까지 전체. 코드는 resolve_product_keyword 로 찾는다
        country: 글의 나라 코드(ISO 두 글자, 예: KR · US · DE) — 선택
        category: VOC 주제 — battery · camera · display · performance · software · build_quality · price · design ·
            connectivity · ai_features · accessories · review · comparison(경쟁 비교) · model_mention(모델 언급) ·
            others(사전 밖 주제) · non_target(대상 밖 글 표지). 기기 종류(phone · watch …)가 아니다 — 선택
        sentiment: 감성 — positive | negative | neutral (생략하면 전체)
        platform: 수집 플랫폼 코드(예: reddit · youtube · gsmarena — 목록은 voc_breakdown(by="platform")) — 선택
        keyword: 본문 낱말(글자 그대로 — 한국어는 원문 부분일치, 영어는 번역문 전문검색) — 선택
        start_date: 발행일 시작 YYYY-MM-DD(이 날 포함) — 선택
        end_date: 발행일 끝 YYYY-MM-DD(이 날 제외) — 선택
        limit: 한 쪽의 행 수(1 ~ 100, 기본 20)
        cursor: 앞 호출의 next_cursor — 다음 쪽(생략하면 처음부터)
    """
    offset = int(cursor or 0)
    rows = await query_voc_tool(product_code, country, category, sentiment, platform,
                                keyword, start_date, end_date, limit + 1, offset)
    out = _listed(rows, limit)
    if not out["truncated"]:
        out["total"] = offset + len(out["results"])
    out["next_cursor"] = str(offset + limit) if out["truncated"] else None
    return out


@mcp.tool()
async def get_top_issues(
    product_code: str,
    period_days: Days = 30,
    top_n: Annotated[int, Field(ge=1, le=20)] = 10,
) -> TopIssuesOut:
    """한 제품의 최근 N일 VOC 를 주제(배터리 · 카메라 · 화면 …)별로 세어 많이 말해진 순서와 부정 비율을 돌려줍니다.

    한 제품에서 무엇이 화제인지 볼 때 씁니다. 그림 · 나라 드릴다운 · 전 기간은 chart_category_distribution, 부품 × 증상
    결함은 defect_profile, 제품끼리 견주기는 compare_products, 글 요약은 get_voc_summary 를 씁니다. 제품이 대표로 붙은 글만
    세고 기간은 수집 시각 기준입니다. comparison · model_mention(글의 성격 표지)은 뺍니다. 한 글에 주제가 여럿이면 각각 세어
    합이 글 수보다 큽니다.

    돌려주는 꼴 — {product_code, period_days, total(주제 수), truncated, results[{category, total_count, negative_rate(%)}]}.
    모르는 제품이거나 기간에 글이 없으면 results 가 빕니다.

    Args:
        product_code: 제품 코드(예: GS25U) — resolve_product_keyword 로 찾는다
        period_days: 기간 — 최근 N일(수집 시각 기준, 1 ~ 3650, 기본 30)
        top_n: 주제 수(1 ~ 20, 기본 10)
    """
    rows = await get_top_issues_tool(product_code, period_days, top_n)
    return {"product_code": product_code.upper(), "period_days": period_days, **_listed(rows, top_n)}


# 시맨틱 검색이 싣는 푼 길 · 일부러 찾지 않은 0건의 까닭(SFO-1 · 3) — search_voc 응답에 그대로 옮긴다
_ROUTE_KEYS = ("reason", "core_key", "matched_by", "via", "resolutions", "core_unresolved")


@mcp.tool()
async def search_voc(
    keyword: str,
    product_code: Optional[str] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 30,
    days: Optional[AnyDays] = None,
    order: SearchOrder = "relevance",
    match: MatchMode = "auto",
    semantic: bool = True,
    strict: bool = False,
    core_type: Optional[CoreType] = None,
    core_key: Optional[str] = None,
) -> dict:
    """VOC 를 뜻으로 찾습니다 — 한국어 · 영어 · 섞어 쓴 자연어를 개념(제품 · 세대 · 부품 · 증상 · 주제 · 시장 · 채널)으로 풀어 찾습니다.

    표기 · 언어가 달라도 뜻이 같으면 결과가 같습니다('S25 울트라 발열' = 'Galaxy S25 Ultra overheating'). 세대('S25 시리즈'),
    비교('S25 vs 아이폰 16 배터리' — 둘 다 언급한 글), 시장('인도'), 채널('레딧에서'), 모델코드(SM-S938B), 은유('폰이 손난로
    같아요' → 발열)도 풉니다. PLM 거울이 연결된 서버는 프로젝트 key · 과제코드 · 과제명 · 개발모델명으로도 찾습니다(연결
    여부는 get_ontology 의 plm). 무엇으로 풀었는지 보려면 semantic_search, 글자 그대로의 필터 목록은 query_voc, 건수 ·
    추세는 keyword_stats, 제품 코드 확정은 resolve_product_keyword 를 씁니다.

    돌려주는 꼴 — {total, truncated, results, strategy, match_mode, notes}, 범위로 푼 말이 있으면 core_key{type_slug, key} ·
    matched_by · via · resolutions · core_unresolved. total 은 같은 본문을 접기 전 전체 건수, results 는 관련도 순 상위
    limit 건이고 행마다 matched(왜 걸렸나)가 있습니다. 0건이면 notes 에 까닭이 있고, reason 이 있으면 범위를 못 걸어 일부러
    찾지 않은 것입니다(in_core_no_local_data · product_not_in_catalog · not_in_core_mirror · no_core_mirror ·
    unsupported_core_type · bad_arguments · semantic_unavailable) — 0건을 'VOC 없음' 으로 단정하지 않습니다. strategy 가
    keyword 면 개념을 못 찾아 낱말 검색으로 간 것이니 notes 를 함께 전합니다. product_name 이 비면 본문에 모델명이 없는
    글입니다. 작성자 · 계정 · 연락처 꼴은 가려서 나갑니다.

    Args:
        keyword: 검색어(자연어 그대로, 예: 'S25 울트라 발열 불만' · '최근 폴드 주름'). core_type 을 주면 나머지 조건만(빈 글자도 된다)
        product_code: 제품 범위 강제(해석한 제품보다 우선) — 선택
        limit: 결과 수(1 ~ 100, 기본 30)
        days: 최근 N일(발행일 기준, 최대 3650) — 생략하거나 0 이면 전 기간. semantic=True 면 검색어의 '최근 · 요즘'(90일) ·
            '이번 주'(7일) · '이번 달'(30일) · '2025년' 도 기간으로 본다
        order: relevance(관련도 → 최신, 기본) | recent(최신순) | engagement(참여도순)
        match: 낱말 검색으로 갈 때의 경로 — auto(기본) | phrase(구절) | fts(영어 번역문 전문검색) | substring(부분일치)
        semantic: False 면 온톨로지 없이 낱말 검색만 한다(core_type 을 주면 무시)
        strict: True 면 알아들은 범위 말(PLM 과제 · 제품)을 못 걸 때 낱말 검색으로 넓히지 않고 0건 + reason
        core_type: 코어 객체로 범위 걸기 — plm_project | plm_task | plm_model (core_key 와 함께). 못 풀면 다른 조건이 있어도
            0건 + reason. 시맨틱 검색으로만 찾는다(semantic=False 여도)
        core_key: 그 코어 객체의 key(옛 key 도 받는다)
    """
    if bool(core_type) != bool(core_key):
        return {"total": 0, "truncated": False, "results": [], "strategy": "semantic", "match_mode": "none",
                "reason": "bad_arguments", "notes": ["core_type 과 core_key 는 함께 준다"]}
    if not semantic and not core_type:
        d = await search_voc_detail(keyword, product_code, limit, days,
                                    "recent" if order == "relevance" else order, match)
        return {"total": d["total"], "truncated": d["truncated"], "results": d["results"], "strategy": "keyword",
                "match_mode": d["match_mode"], "notes": d["notes"]}
    r = await semantic_search_tool(keyword, product_code, limit, days, order, match, strict, core_type, core_key)
    return {"total": r["total"], "truncated": r["total"] > len(r["results"]), "results": r["results"],
            "strategy": r["strategy"], "match_mode": r.get("match_mode") or r["strategy"],
            "notes": (r.get("interpretation") or {}).get("notes", []),
            **{k: r[k] for k in _ROUTE_KEYS if k in r}}


@mcp.tool()
async def semantic_search(
    query: str,
    product_code: Optional[str] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 30,
    days: Optional[AnyDays] = None,
    order: SearchOrder = "relevance",
    strict: bool = False,
) -> dict:
    """search_voc 와 같은 뜻 검색을 하고, 검색어를 무엇으로 풀었는지(해석)를 함께 돌려줍니다.

    'S25' 를 기본 모델로 봤는지 세대 전체로 봤는지, 어떤 동의어로 넓혔는지 확인하거나 사용자에게 설명할 때 씁니다. 결과만
    필요하면 search_voc(match · semantic · core_type 인자도 있다), 개념 사전은 get_ontology 를 씁니다.

    돌려주는 꼴 — {query, strategy, total, truncated, results, interpretation, elapsed_ms} 와 search_voc 의 범위 칸(core_key ·
    matched_by · via · resolutions · core_unresolved · reason). interpretation 은 concepts[{type, key, label, via, role}] (role —
    scope 범위 · match 후보 · boost 가산 · derived 관계로 추가 · inferred 추론), scope(건 제품 범위), expansion(증상 · 부품별
    다국어 동의어), notes(판단 근거), llm_symptoms(사전에 없는 표현에서 추론한 증상 — 사전 증상의 절반으로 친다), vector(임베딩
    사용 여부 · 후보 수 — 사전이 질의를 다 설명하면 쓰지 않는다)입니다. strategy 는 semantic(개념) · vector(개념 없이 임베딩 ·
    낱말) · keyword(낱말 검색). total 은 같은 본문을 접기 전 전체 건수, results 는 관련도 순 상위 limit 건입니다.

    Args:
        query: 검색어(자연어)
        product_code: 제품 범위 강제 — 선택
        limit: 결과 수(1 ~ 100, 기본 30)
        days: 최근 N일(발행일 기준, 최대 3650) — 생략하거나 0 이면 전 기간(검색어의 '최근' 등은 기간으로 본다)
        order: relevance(관련도 → 최신, 기본) | recent(최신순) | engagement(참여도순)
        strict: True 면 알아들은 범위 말을 못 걸 때 넓히지 않고 0건 + reason
    """
    r = await semantic_search_tool(query, product_code, limit, days, order, strict=strict)
    return {**r, "truncated": r.get("total", 0) > len(r.get("results") or [])}


@mcp.tool()
async def get_ontology(concept: Optional[str] = None) -> dict:
    """VOC 온톨로지(개념 사전과 관계)를 둘러봅니다 — 전체 구조나 개념 하나의 정의 · 관계 · 말뭉치 규모.

    검색 · 결함 도구에 넣을 key(부품 · 증상 · 주제 · 제품 코드)를 찾거나 PLM 거울이 켜졌는지 볼 때 씁니다. 질의 하나가 어떻게
    풀리는지는 semantic_search, 부품 × 증상의 실제 건수는 defect_profile, 제품명 → 코드는 resolve_product_keyword 를 씁니다.

    concept 없이 부르면 전체 구조 — 개념 종류, symptoms(증상마다 함의 부품 · 심각도 · 주제 · 본문 검색어 · 주석된 VOC 수),
    components, topics, relations, catalog(제품 · 플랫폼 수), model_codes(모델코드 사전 크기), plm(PLM 거울 — active · 과제 ·
    개발모델 수 · 마지막으로 받은 시각 · last_result ok | check | error · problems). plm.active 가 false 면 과제코드 · 과제명
    검색이 꺼져 있습니다.
    concept 를 주면 그 개념 하나(type symptom · component · topic · product)의 정의와 VOC 수입니다. 같은 낱말이 부품이자
    주제면(battery · camera · display · software · connectivity) 부품으로 보니, 주제는 'topic:battery' 처럼 종류를 앞에
    붙입니다. key 가 아니면 질의로 풀어 해석을 돌려줍니다(type query).

    Args:
        concept: 개념 key(예: overheat · hinge · GS25U · 'topic:battery' · 'symptom:burn_in') 또는 자연어 — 생략하면 전체 구조
    """
    return await get_ontology_tool(concept)


@mcp.tool()
async def resolve_product_keyword(keyword: Annotated[str, Field(min_length=1)]) -> ResolveOut:
    """제품명 · 별칭 · 모델코드를 카탈로그의 제품 코드로 확정하고, 같은 말의 낱말 검색 건수와 태깅 건수를 견줘 줍니다.

    제품을 묻는 질문은 먼저 이 도구로 코드를 정한 뒤 다른 도구의 product_code 로 넘깁니다 — 자유 텍스트 낱말 검색은
    표기('S25 Ultra' · 'S25Ultra' · '갤럭시 S25 울트라')에 따라 건수가 크게 흔들립니다. 세대 · 형제 모델은
    get_ontology('<코드>'), 출시 후 추이는 get_product_timeline · lifecycle_compare 를 씁니다.

    찾는 차례 — 카탈로그 이름 · 코드 → 태깅과 같은 별칭 사전 → PLM 거울(연결된 서버 — 프로젝트 key · 옛 key · 이름, 과제코드 ·
    과제명, 개발모델명 → 모델코드 → 제품).
    돌려주는 꼴 — resolved_code · resolved_via(catalog_name | alias | plm) · tagged_count(대표 제품으로 붙은 글 수) ·
    keyword_hits(낱말 검색 건수 — 시간 상한을 넘기면 표본 어림이고 keyword_hits_exact 가 false) · candidates(최대 5) ·
    status(resolved | no_catalog_match | no_tagged_records) · note, 푼 길 core_key{type_slug, key} · matched_by · via ·
    resolutions. PLM 으로 풀면 resolved_codes(제품 전부), PLM 이 알아들었지만 이 서버의 제품에 못 이으면 reason
    in_core_no_local_data 와 core_unresolved 입니다.

    Args:
        keyword: 제품명 · 별칭 · 코드 · 모델코드 중 아무것(예: 'S25 울트라' · 'Galaxy Z Fold8' · 'GZF8' · 'SM-S938B')
    """
    return await resolve_product_keyword_tool(keyword)


# ── 분석 ──────────────────────────────────────────────────

@mcp.tool()
async def analyze_sentiment_trend(
    product_code: str,
    period_days: Days = 90,
    granularity: Interval = "week",
    start_date: Optional[IsoDate] = None,
    end_date: Optional[IsoDate] = None,
) -> SentimentTrendOut:
    """한 제품의 감성(긍정 · 부정 · 중립 건수와 평균 점수)을 일 · 주 · 월 구간별 시계열로 돌려줍니다.

    한 제품의 여론이 언제 나빠지고 좋아졌는지 볼 때 씁니다. start_date · end_date 로 분기 · 연도 같은 임의 구간을, 생략하면
    최근 period_days 일을 봅니다(발행일 기준). 여러 제품의 건수 선은 chart_sentiment_timeseries, 달력 월별 생애 곡선은
    get_product_timeline, 직전 세대와 같은 출시 후 주차 비교는 lifecycle_compare, 낱말 · 결함의 추이는 keyword_stats ·
    defect_timeline 을 씁니다. 제품이 대표로 붙은 글만 셉니다.

    돌려주는 꼴 — {product_code, granularity, range{start_date, end_date, period_days}, data[{period(구간 시작일), positive,
    negative, neutral, avg_score(-1 ~ 1)}]}. 구간을 자르지 않고, 글이 없는 구간은 빠집니다. 모르는 제품이면 data 가 빕니다.

    Args:
        product_code: 제품 코드(예: GS25U)
        period_days: 기간 — 최근 N일(1 ~ 3650, 기본 90). start_date · end_date 를 주면 쓰지 않는다
        granularity: 구간 — day | week | month (기본 week)
        start_date: 발행일 시작 YYYY-MM-DD(이 날 포함) — 선택
        end_date: 발행일 끝 YYYY-MM-DD(이 날 제외) — 선택
    """
    return await analyze_sentiment_trend_tool(
        product_code, period_days, granularity, start_date, end_date)


@mcp.tool()
async def compare_products(
    product_codes: Annotated[list[str], Field(min_length=1, max_length=10)],
    category: Optional[Topic] = None,
) -> dict:
    """제품 여럿의 최근 30일 VOC 평균 감성을 주제(배터리 · 카메라 …)별로 나란히 돌려줍니다.

    제품끼리 어느 주제에서 평이 갈리는지 견줄 때 씁니다. 건수 · 부정 비율 · 비교글까지 세려면 voc_breakdown(by="product",
    include_mentions=True), 추세는 chart_sentiment_timeseries, 늘고 준 것은 period_compare(by="product"), 같은 출시 후
    주차 비교는 lifecycle_compare 를 씁니다. 기간은 수집 시각 기준 최근 30일로 정해져 있고 제품이 대표로 붙은 글만 셉니다.

    돌려주는 꼴 — {제품 코드: {주제: 평균 감성(-1 ~ 1)}} — 감성 높은 주제부터, 감성 점수가 없는 주제는 null. category 를 주면
    그 주제만 담습니다. 글이 없는 제품은 빈 객체입니다. 건수는 주지 않습니다.

    Args:
        product_codes: 비교할 제품 코드(1 ~ 10개, 예: ["GS25U", "GZF7"]) — resolve_product_keyword 로 찾는다
        category: VOC 주제 하나로 좁히기(query_voc 의 category 와 같다 — 기기 종류가 아니다) — 선택
    """
    return await compare_products_tool(product_codes, category)


@mcp.tool()
async def get_country_breakdown(
    product_code: str,
    period_days: Days = 30,
) -> CountryBreakdownOut:
    """한 제품의 VOC 를 나라별로 세어 건수 · 평균 감성 · 긍정 비율을 돌려줍니다(최대 30개국).

    한 제품이 어느 시장에서 많이 말해지고 평이 어떤지 볼 때 씁니다. 비중 · 부정 비율 · 필터 조합은
    voc_breakdown(by="country", product_code=…), 그림 · 전체 제품은 chart_country_distribution, 결함만은
    defect_breakdown(by="country"), 전 기간 제품 ↔ 나라 관계는 get_kg_relations 를 씁니다. 기간은 수집 시각 기준이고
    제품이 대표로 붙은 글만, 나라 코드가 없는 글은 빼고 셉니다.

    돌려주는 꼴 — {product_code, period_days, total(나라 수), truncated, results[{country_code, voc_count, avg_score(-1 ~ 1),
    positive_rate(%)}]} — 많은 나라부터 30개까지.

    Args:
        product_code: 제품 코드(예: GS25U)
        period_days: 기간 — 최근 N일(수집 시각 기준, 1 ~ 3650, 기본 30)
    """
    rows = await get_country_breakdown_tool(product_code, period_days)
    return {"product_code": product_code.upper(), "period_days": period_days, **_listed(rows, 30)}


@mcp.tool()
async def get_voc_summary(
    product_code: str,
    period_days: Days = 7,
) -> str:
    """한 제품의 최근 N일 VOC 를 세 줄 글로 요약합니다 — 총 건수 · 긍정과 부정 비율 · 평균 감성 · 주요 주제 다섯.

    사람에게 바로 보여 줄 짧은 요약이 필요할 때 씁니다. 수치를 다시 쓸 거면 get_top_issues · analyze_sentiment_trend, 전체
    제품의 하루 요약은 daily_briefing, 결함은 defect_profile 을 씁니다. 기간은 수집 시각 기준이고 제품이 대표로 붙은 글만
    셉니다.

    돌려주는 꼴 — 글 한 덩어리. 0건이면 까닭(unknown_product | no_tagged_records | none_in_period | no_records)을 한 줄로
    말합니다.

    Args:
        product_code: 제품 코드(예: GS25U)
        period_days: 기간 — 최근 N일(수집 시각 기준, 1 ~ 3650, 기본 7)
    """
    return await get_voc_summary_tool(product_code, period_days)


# ── 인사이트 / 운영 도구 ──────────────────────────────────────────

@mcp.tool()
async def daily_briefing(
    date: Optional[Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]] = None,
) -> str:
    """하루(KST 달력일)에 수집된 VOC 를 한 문서로 요약한 글을 돌려줍니다.

    전체 제품을 한눈에 볼 때 씁니다. 한 제품이면 get_voc_summary, 수치를 다시 쓸 거면 corpus_overview,
    지금 걸린 경보는 alert_check, 전기 대비 변화는 period_compare 를 씁니다. 날짜는 수집 시각 기준입니다(발행일이 아님).

    돌려주는 글 — 전체 요약(신규 건수 · 활성 플랫폼 · 언급 제품 · 감성 분포), 언급량 상위 5 제품, 핫 카테고리 상위 5,
    부정 의견이 많은 제품 상위 3(없으면 그 절이 빠진다). 제품 절은 제품이 붙은 글만 셉니다.

    Args:
        date: 날짜 YYYY-MM-DD(KST) — 생략하면 오늘
    """
    return await daily_briefing_tool(date)


@mcp.tool()
async def alert_check() -> AlertCheckOut:
    """지금 걸린 경보 세 가지를 한 번에 돌려줍니다 — 부정 비율이 높은 제품, 부정이 급증한 제품, 수집이 멈춘 플랫폼.

    임계치(부정 비율 · 최소 건수 · 급증 배수 · 정체 시간)는 결과의 thresholds 에 있고, 창은 지금부터 거꾸로 24시간과 그 앞
    24시간입니다(수집 시각 기준). 급증은 24h 부정이 최소 건수 이상이면서 직전 24h 의 배수 이상이거나 직전이 0 일 때입니다.
    제품 결함의 수명 기준 급등은 defect_anomalies, 새로 생긴 경보만 이어 받기는 list_changes, 수집 운영 전체는 site_health,
    기간 대비 늘고 준 것은 period_compare 를 씁니다.

    돌려주는 꼴 — 목록은 제품 10개 · 플랫폼 15개까지이고 *_total 이 전체 수입니다(요약도 전체 수로 말합니다).
    """
    return await alert_check_tool()


@mcp.tool()
async def site_health() -> SiteHealthOut:
    """플랫폼(사이트)별 수집 상태와 수집 워커가 살아 있는지를 돌려줍니다.

    수집이 멈췄는지, 어느 사이트가 정체인지 볼 때 씁니다. 경보만 보면 alert_check, 채널별 말의 양 · 감성은
    get_platform_breakdown 이나 voc_breakdown(by="platform") 을 씁니다.

    돌려주는 꼴 — platforms 는 플랫폼 전부(자르지 않음)이고 각 행에 24h 수집량 · 부정 건수 · 평균 감성 · 전 기간 마지막 수집
    시각 · 상태(healthy | stale | no_data_ever)가 있습니다. stale 은 마지막 수집이 stale_hours 를 넘은 것으로 alert_check 와
    같은 기준이며 비활성 플랫폼도 셉니다. worker.status 는 alive | stalled(마지막 작업이 3시간 넘음) | never_ran(이 서버는
    수집하지 않고 덤프로만 받는다 — 마지막 수집 시각은 원천 서버의 것)입니다.
    """
    return await site_health_tool()


@mcp.tool()
async def top_emerging_keywords(
    period_days: Annotated[int, Field(ge=1, le=365)] = 7,
    product_code: Optional[str] = None,
    top_n: Annotated[int, Field(ge=1, le=50)] = 20,
) -> EmergingKeywordsOut:
    """최근 N일에 수집된 VOC 본문에서 한국어 · 영어 낱말을 세어 빈도 상위 N개를 돌려줍니다.

    무엇이 많이 말해지는지 훑을 때 씁니다 — 이름과 달리 전기와 견주지 않습니다. 한 낱말이 정말 느는지는 keyword_stats,
    늘고 준 제품 · 채널은 period_compare, 낱말 사이 관계는 chart_keyword_network, 구조화된 결함은 defect_profile, 원문은
    search_voc 를 씁니다. 제품이 붙은 글만 셉니다.

    돌려주는 꼴 — 표본은 최신 글부터 row_limit 건까지라 sample_capped 가 참이면 기간 전체가 아니라 최근 쪽만 본 것입니다.
    영어는 원문과 번역문을 함께 셉니다. distinct_* 는 자르기 전 낱말 수이고 truncated 가 잘렸는지입니다.

    Args:
        period_days: 기간 — 최근 N일(수집 시각 기준, 1 ~ 365, 기본 7)
        product_code: 제품 코드로 범위 한정 — 선택
        top_n: 언어별 낱말 수(1 ~ 50, 기본 20)
    """
    return await top_emerging_keywords_tool(period_days, product_code, top_n)


@mcp.tool()
async def list_changes(
    since: Optional[Annotated[int, Field(ge=0)]] = None,
    limit: Annotated[int, Field(ge=1, le=200)] = 50,
    events: Optional[list[EventName]] = None,
) -> ChangesOut:
    """SignalForge 에 새로 생긴 일(결함 급등 · 사이트 수집 이상 · 운영 지표 경보 · 백업 검증 실패 · 지표 규칙 경보)을 id 커서로 이어 읽게 돌려줍니다.

    '지난번 뒤로 바뀐 것' 만 받을 때 씁니다 — 게이트웨이가 주기적으로 물어 이벤트로 다시 냅니다. since 를 주면 그 id 뒤의 일을
    오래된 것부터, 생략하면 가장 최근 limit 건을 줍니다. 받은 next_cursor 를 다음 since 로 넘기면 빠짐 · 겹침 없이 이어집니다.
    결함 급등의 자세한 값은 defect_anomalies, 지금 걸린 감성 경보는 alert_check, 수집 상태 전체는 site_health 를 씁니다.

    이벤트 이름(signalforge.<대상>.<일>) — signalforge.defect.spiked(결함 급등) · signalforge.collection.degraded(사이트 수집
    이상) · signalforge.operations.alerted(운영 지표 경보) · signalforge.ops_status.violated(운영 상태 위반) ·
    signalforge.backup.failed(백업 검증 실패) · signalforge.alert.fired(지표 규칙 경보).
    돌려주는 꼴 — {total(since 뒤 전체 수), truncated, next_cursor, events[{id, event, rule, severity, at, value, threshold, summary,
    detail}]}. since 를 주면 truncated 는 뒤에 더 있다는 뜻이고(next_cursor 로 다시 부른다), 생략하면 그보다 오래된 일이 더
    있다는 뜻입니다. at 은 일이 난 시각이고 id 는 이 서버에 기록된 차례라, 덤프로 받는 서버에서는 at 순서와 다를 수 있습니다 —
    커서는 늘 id 입니다. 이 서버가 화면에서 숨긴 규칙(ALERT_HIDDEN_RULES)은 내지 않습니다.

    Args:
        since: 앞 호출의 next_cursor(이벤트 id) — 이 id 뒤의 일만. 생략하면 가장 최근 limit 건
        limit: 건수(1 ~ 200, 기본 50)
        events: 이벤트 이름으로 한정(여럿 가능) — 생략하면 전부
    """
    return await list_changes_tool(since, limit, events)


# ── 그래프 규격 도구 — echarts_option 반환 ──
# 모든 도구가 {chart_type, raw, echarts_option, summary} 반환.
# LLM 은 echarts_option 을 그대로 렌더하거나 raw 로 다른 라이브러리에 매핑.
@mcp.tool()
async def chart_sentiment_timeseries(
    product_codes: Annotated[list[str], Field(min_length=1, max_length=8)],
    days: AnyDays = 90,
    granularity: Interval = "week",
) -> ChartOut:
    """제품 여럿의 VOC 건수 추이를 라인 차트 규격(ECharts option)으로 돌려줍니다 — 감성이 아니라 건수 선입니다.

    제품끼리 말의 양이 언제 늘고 줄었는지 견줄 때 씁니다. 감성 비율의 추이는 analyze_sentiment_trend(제품 하나), 출시 뒤
    수명 곡선은 get_product_timeline · lifecycle_compare, 낱말 · 결함의 추이는 keyword_stats · defect_timeline 을 씁니다.
    날짜는 발행일 기준입니다.

    돌려주는 꼴 — raw 는 {per_product{코드: [{period, count, avg_score}]}, periods}. 빈 구간은 0 으로 채우고, 모르는 코드는
    0 선이 됩니다.

    Args:
        product_codes: 제품 코드 목록(1 ~ 8개, 예: ["GS25", "GZF8"])
        days: 기간 — 최근 N일(0 이면 전 기간, 최대 3650, 기본 90)
        granularity: 구간 — day | week | month (기본 week)
    """
    return await chart_sentiment_timeseries_tool(product_codes, days, granularity)


@mcp.tool()
async def chart_country_distribution(
    product_code: Optional[str] = None,
    top_n: Annotated[int, Field(ge=1, le=50)] = 15,
    days: AnyDays = 0,
) -> ChartOut:
    """국가별 VOC 건수를 가로 막대 차트 규격으로 돌려줍니다(제품 하나 또는 전체).

    어느 나라에서 말이 많은지 그림으로 볼 때 씁니다. 인용할 수치(비중 · 부정 비율 · 전체 수)는 voc_breakdown(by="country"),
    한 제품의 국가별 긍정 비율은 get_country_breakdown, 언어별은 get_language_breakdown 을 씁니다. 국가 코드가 없는 글은
    뺍니다. 날짜는 발행일 기준입니다.

    돌려주는 꼴 — raw 는 [{country_code, voc_count, avg_score}] 상위 top_n 개국이고 total 이 국가 수, truncated 가 잘렸는지입니다.

    Args:
        product_code: 제품 코드로 한정 — 선택(생략하면 제품이 안 붙은 글까지 전체)
        top_n: 상위 국가 수(1 ~ 50, 기본 15)
        days: 기간 — 최근 N일(0 이면 전 기간, 최대 3650, 기본 0)
    """
    return await chart_country_distribution_tool(product_code, top_n, days)


@mcp.tool()
async def chart_category_distribution(
    product_code: Optional[str] = None,
    top_n: Annotated[int, Field(ge=1, le=50)] = 15,
    days: AnyDays = 0,
    country: Optional[str] = None,
) -> ChartOut:
    """이슈 카테고리별 VOC 건수를 가로 막대 차트 규격으로 돌려줍니다(제품 · 나라로 좁힐 수 있음).

    무슨 이야기(배터리 · 카메라 · 화면 …)가 많은지 그림으로 볼 때 씁니다. 한 제품의 이슈 순위와 부정 비율은 get_top_issues,
    제품끼리 카테고리별 감성은 compare_products, 부품 × 증상 결함은 defect_profile · defect_breakdown 을 씁니다.
    voc_breakdown(by="category") 의 category 는 기기 종류(phone · watch …)라 이것과 다릅니다.

    막대는 이슈 카테고리만입니다. comparison(경쟁사 비교) · model_mention(모델 언급)은 글의 성격 표지라 meta_tags 로 따로
    줍니다 — 모델 이름만 나와도 붙어 '최대 이슈' 로 읽으면 틀립니다. 한 글에 태그가 여럿이면 각각 세어 막대 합이 글 수보다
    큽니다.

    돌려주는 꼴 — raw 는 [{category, voc_count, avg_score}] 상위 top_n 개, total 이 이슈 카테고리 수, truncated 가 잘렸는지,
    meta_tags · meta_note 가 성격 표지입니다.

    Args:
        product_code: 제품 코드로 한정 — 선택
        top_n: 상위 카테고리 수(1 ~ 50, 기본 15)
        days: 기간 — 최근 N일(0 이면 전 기간, 최대 3650, 기본 0)
        country: 국가 코드(ISO 두 글자, 예: US)로 드릴다운 — 선택
    """
    return await chart_category_distribution_tool(product_code, top_n, days, country)


@mcp.tool()
async def chart_crisis_timeline(case_code: Optional[CrisisCase] = None) -> ChartOut:
    """과거 위기 사례 다섯의 일별 VOC 건수를 line + area 차트 규격(최고점 표시)으로 돌려줍니다.

    목록에 있는 사례의 정해진 기간을 볼 때 씁니다. 다른 제품 · 기간의 추이는 analyze_sentiment_trend(start_date · end_date),
    결함 추이는 defect_timeline 을 씁니다.

    돌려주는 꼴 — case_code 를 주면 raw 는 {code, title, total_voc, neg_rate(0~1), timeline[{day, count}]} 하나, 생략하면 그
    목록(최대 5)이고 차트는 첫 사례입니다. 글이 없는 날은 timeline 에 없습니다.

    Args:
        case_code: 위기 사례 — GN7 | GZF1 | GS22U | GZFL3 | GS20 (생략하면 다섯 전부)
    """
    return await chart_crisis_timeline_tool(case_code)


@mcp.tool()
async def chart_keyword_network(
    product_code: Optional[str] = None,
    days: Annotated[int, Field(ge=1, le=365)] = 30,
    min_cooccur: Annotated[int, Field(ge=2, le=50)] = 3,
    max_nodes: Annotated[int, Field(ge=5, le=80)] = 40,
) -> ChartOut:
    """같은 글에 함께 나온 키워드 쌍으로 지은 관계망을 force-graph 차트 규격으로 돌려줍니다.

    낱말끼리 어떤 무리로 묶여 말해지는지 볼 때 씁니다. 결함끼리의 동시 발생은 defect_cooccurrence, 제품 ↔ 카테고리 · 나라 ·
    플랫폼 관계는 get_kg_relations, 낱말 빈도는 top_emerging_keywords 를 씁니다. 무리는 이어진 성분입니다.

    돌려주는 꼴 — raw 는 {nodes[{id, name, value, category, lang}], edges[{source, target, value}], meta}. 노드는 가중 차수
    상위 max_nodes 개만 남기고 그 사이 엣지만 싣습니다 — meta 의 total_nodes · total_edges · truncated 가 자르기 전 수와
    잘렸는지, min_cooccur · max_nodes 가 실제로 쓴 값입니다.

    Args:
        product_code: 제품 코드로 한정 — 선택
        days: 기간 — 최근 N일(발행일 기준, 1 ~ 365, 기본 30)
        min_cooccur: 엣지로 칠 최소 동시 출현 수(2 ~ 50, 기본 3)
        max_nodes: 노드 수 상한(5 ~ 80, 기본 40)
    """
    return await chart_keyword_network_tool(product_code, days, min_cooccur, max_nodes)


# ── 차원 분해 도구 — 플랫폼 · 참여도 · 언어 ──
@mcp.tool()
async def get_platform_breakdown(
    product_code: Optional[str] = None,
    period_days: AnyDays = 30,
    top_n: Annotated[int, Field(ge=1, le=100)] = 20,
) -> PlatformBreakdownOut:
    """플랫폼(커뮤니티 · 사이트)별 VOC 건수 · 평균 감성 · 부정 비율을 돌려줍니다(제품 하나 또는 전체).

    어느 채널에서 말이 많고 평이 나쁜지 볼 때 씁니다. 비중 · 필터 조합 · 플랫폼 종류별은 voc_breakdown(by="platform" ·
    "platform_kind"), 결함만은 defect_breakdown(by="platform"), 수집이 멈췄는지는 site_health, 기간 대비 증감은
    period_compare(by="platform") 를 씁니다. 기간은 수집 시각 기준이라 옛 글을 최근에 모은 것도 들어갑니다.

    돌려주는 꼴 — {total(플랫폼 수), truncated, results[{platform_code, platform(표시 이름), voc_count, avg_score(-1 ~ 1),
    negative_rate(%)}]} — 많은 플랫폼부터. platform_code 는 다른 도구의 platform 인자에 그대로 씁니다.

    Args:
        product_code: 제품으로 한정 — 생략하면 제품이 안 붙은 글까지 전체
        period_days: 기간 — 최근 N일(수집 시각 기준, 0 이면 전 기간, 최대 3650, 기본 30)
        top_n: 플랫폼 수(1 ~ 100, 기본 20)
    """
    return _listed(await get_platform_breakdown_tool(product_code, period_days, top_n), top_n)


@mcp.tool()
async def get_engagement_leaders(
    product_code: Optional[str] = None,
    period_days: AnyDays = 30,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> EngagementLeadersOut:
    """참여도(좋아요 · 댓글 · 공유) 점수가 가장 높은 VOC 글을 돌려줍니다 — 많이 퍼진 · 영향 큰 글.

    바이럴 글이나 여론을 이끈 글을 찾을 때 씁니다. 주제로 좁혀 참여도순으로 보려면 search_voc(order="engagement"), 필터
    목록은 query_voc, 결함 1인칭 원문은 defect_evidence 를 씁니다. 참여 수치가 있는 글만(점수 > 0) 보고, 기간은 수집 시각
    기준이라 옛 글이 섞일 수 있습니다(행의 published_at 을 봅니다).

    돌려주는 꼴 — {total, truncated, results[{id, source_url, author_name, platform_name, country_code, sentiment_label,
    likes_count, comments_count, shares_count, engagement_score(0 ~ 100, 로그 눈금), snippet(200자), published_at}]} — 점수
    높은 순 limit 건. 더 있으면 truncated 가 참이고 total 은 null 입니다. 작성자 · 계정은 가려서 나갑니다.

    Args:
        product_code: 제품으로 한정 — 선택(생략하면 전체)
        period_days: 기간 — 최근 N일(수집 시각 기준, 0 이면 전 기간, 최대 3650, 기본 30)
        limit: 글 수(1 ~ 100, 기본 20)
    """
    return _listed(await get_engagement_leaders_tool(product_code, period_days, limit + 1), limit)


@mcp.tool()
async def get_language_breakdown(
    period_days: AnyDays = 0,
    top_n: Annotated[int, Field(ge=1, le=60)] = 25,
) -> LanguageBreakdownOut:
    """감지한 언어별 VOC 건수와 평균 감성을 돌려줍니다 — 말뭉치가 어느 언어권을 덮는지.

    언어 분포를 볼 때 씁니다. 필터(제품 · 나라 · 낱말)를 걸거나 비중 · 부정 비율이 필요하면 voc_breakdown(by="language"),
    나라별은 get_country_breakdown · chart_country_distribution 을 씁니다. 언어는 ISO 639-1 두 글자(ko · en · de …)이고,
    10자 미만이거나 감지에 실패한 글은 en 으로 들어가 en 이 실제보다 큽니다. 언어가 비어 있는 글은 빼고, 기간은 수집 시각
    기준입니다.

    돌려주는 꼴 — {total(언어 수), truncated, results[{language, voc_count, avg_score(-1 ~ 1)}]} — 많은 언어부터.

    Args:
        period_days: 기간 — 최근 N일(수집 시각 기준, 0 이면 전 기간, 최대 3650, 기본 0)
        top_n: 언어 수(1 ~ 60, 기본 25)
    """
    return _listed(await get_language_breakdown_tool(period_days, top_n), top_n)


# ── 생애주기 · 지식그래프 · 태깅 점검 ──
@mcp.tool()
async def get_product_timeline(
    product_code: str,
    recent_months: Annotated[int, Field(ge=0, le=240)] = 0,
) -> ProductTimelineOut:
    """한 제품의 VOC 를 달력 월별로 세어 건수 · 평균 감성 · 부정 비율의 생애 추이를 돌려줍니다.

    제품이 나온 뒤 말의 양과 평이 달마다 어떻게 변했는지 볼 때 씁니다. 직전 세대와 같은 출시 후 주차로 견주려면
    lifecycle_compare, 임의 구간 · 일과 주 단위는 analyze_sentiment_trend, 여러 제품은 chart_sentiment_timeseries 를 씁니다.
    달은 발행일의 달력 월이고(출시 후 경과 월이 아님) released_at 은 참고용 출시일입니다. 미리 계산한 집계라 최근 글은 한
    시간쯤 늦게 반영됩니다.

    돌려주는 꼴 — {product_code, name_ko, series, released_at, months(글이 있는 달 수), timeline[{month(YYYY-MM-01), voc_count,
    sent_avg(-1 ~ 1), neg_rate(0 ~ 1)}]} — 오래된 달부터, 글이 없는 달은 빠집니다. 모르는 코드이거나 글이 없으면 timeline 이
    비고 note 가, 집계가 아직 없으면 status aggregating 이 붙습니다.

    Args:
        product_code: 제품 코드(예: GS25)
        recent_months: 최근 N개월만(이번 달 포함, 최대 240) — 0 이면 전체(기본 0)
    """
    return await get_product_timeline_tool(product_code, recent_months)


@mcp.tool()
async def get_kg_relations(
    node: Optional[str] = None,
    edge_type: Optional[KgEdgeType] = None,
    top_n: Annotated[int, Field(ge=1, le=100)] = 30,
) -> KgRelationsOut:
    """제품과 주제 · 나라 · 플랫폼 사이의 관계(잇는 VOC 수와 평균 감성)를 전 기간으로 모아 무게 순서로 돌려줍니다.

    '어느 나라 · 플랫폼 · 주제가 이 제품과 가장 많이 묶이나', 거꾸로 '인도와 묶인 제품은'(node='country:IN') 같은 전 기간 관계를
    볼 때 씁니다. 기간을 정한 분포는 voc_breakdown(product_code=…, by="country" · "platform"), 주제 순위는 get_top_issues ·
    chart_category_distribution, 낱말 관계망은 chart_keyword_network, 개념 사전은 get_ontology 를 씁니다. 미리 계산한
    집계라 최근 글은 반 시간쯤 늦게 들어오고, 제품이 붙은 글만 셉니다.

    node 꼴 — 'product:<코드>' · 'category:<주제>' · 'country:<ISO 두 글자 대문자>' · 'platform:<플랫폼 코드>'. 글자 그대로
    맞춥니다(대소문자 구분).
    돌려주는 꼴 — {total(관계 수), truncated, results[{edge_type, source, target, weight(잇는 VOC 수), sent_avg(-1 ~ 1)}]} —
    무게 순 top_n 개. 집계가 아직 없으면 results 가 비고 status aggregating · note 가 붙습니다.

    Args:
        node: 관계의 한쪽 끝(source 또는 target, 예: 'product:GS25' · 'country:US') — 선택
        edge_type: 관계 종류 — product_category | product_country | product_platform — 선택
        top_n: 관계 수(1 ~ 100, 기본 30)
    """
    rows = await get_kg_relations_tool(node, edge_type, top_n)
    if rows and rows[0].get("status") == "aggregating":
        return {"total": 0, "truncated": False, "results": [], **rows[0]}
    return _listed(rows, top_n)


@mcp.tool()
async def get_unmapped_voc(
    reason: Optional[UnmappedReason] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> UnmappedVocOut:
    """제품이 안 붙은 VOC 를 점검합니다 — 지금의 제품 태깅 비율과, 안 붙은 글의 사유별 수와 표본.

    태깅 비율을 말해야 하거나 '왜 이 글에 제품이 없나' 를 볼 때 씁니다(숫자는 도구 설명이 아니라 이 결과의 coverage 를
    인용합니다). 기간을 정한 태깅 비율은 corpus_overview(days=…), 제품명을 코드로 바꾸려면 resolve_product_keyword 를 씁니다.
    제품이 안 붙은 글의 대부분은 처리 실패가 아니라 본문에 특정 모델명이 없는 글입니다.

    돌려주는 꼴 — {coverage{active_total, tagged, untagged, tagged_pct}(전 기간), untagged_total, breakdown[{reason, count}]
    (사유 전부 — 사유가 비어 있는 글은 '(unclassified)'), samples[{id, platform_name, country_code, unmapped_reason,
    sentiment_label, snippet(160자)}](참여도 높은 순 limit 건), note}.

    Args:
        reason: 표본을 이 사유로 한정 — no_model_mention(모델명 없음) | non_galaxy(갤럭시 아님) | too_short(너무 짧음) |
            noise(잡음) — 선택
        limit: 표본 수(1 ~ 100, 기본 20)
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
    component: Optional[Component] = None,
    severity: Optional[Severity] = None,
    period_days: AnyDays = 90,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> DefectProfileOut:
    """결함을 (제품, 부품, 증상, 심각도) 조합으로 묶어 글 수가 많은 순서로 돌려줍니다 — 무엇이 고장나는가.

    결함 조사의 첫 단계로 씁니다. 이어서 언제부터 · 가속 중인지는 defect_timeline, 누가 어디서 말하는지는 defect_breakdown,
    출시 후 며칠에 터지는지는 defect_onset, 함께 터지는 결함은 defect_cooccurrence, 원문 근거는 defect_evidence 를 씁니다.
    부품 · 증상 key 는 get_ontology 에 있고, 사전 밖 낱말은 keyword_stats 로 셉니다. 제품은 비교글에서 언급된 제품까지
    셉니다. 기간은 발행일 기준입니다.

    돌려주는 꼴 — {pipeline_status, period_days, filters, total(조합 수), truncated, defects[{product_code, component, symptom,
    severity, count(글 수), platforms, effective_platforms}], note}. effective_platforms(1/HHI)는 글이 플랫폼에 고루 퍼진
    정도입니다 — 2 미만이면 한 커뮤니티에 몰린 것이지만, 크다고 독립 제보가 많은 것은 아닙니다(기사 퍼나르기도 퍼진다).
    pipeline_status 가 not_run 이면 빈 결과는 '결함 없음' 이 아니라 '측정 안 함' 입니다.

    Args:
        period_days: 기간 — 최근 N일(발행일 기준, 0 이면 전 기간, 최대 3650, 기본 90)
        limit: 조합 수(1 ~ 100, 기본 20)
    """
    return await defect_profile_tool(product_code, component, severity,
                                     period_days, limit)


@mcp.tool()
async def defect_anomalies(
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    severity: Optional[AlertSeverity] = None,
) -> DefectAnomaliesOut:
    """결함 급등 탐지기가 기록한 급등(제품 × 부품 × 증상)을 최근에 울린 것부터 돌려줍니다.

    '지금 새로 터지는 결함이 있나' 를 볼 때 씁니다. 탐지기는 최근 구간의 1인칭 제보 비중을 기준(직전 세대의 같은 출시 후
    구간, 없으면 자기 과거)과 견주고, 독립 출처가 셋 이상일 때만 기록합니다(한 커뮤니티 쏠림은 거른다). 새로 생긴 것만 이어
    받으려면 list_changes, 직접 세대를 견주려면 lifecycle_compare, 급등의 추이 확인은 defect_timeline, 기준 없는 증감은
    period_compare, 지난 24시간 감성 경보는 alert_check 를 씁니다.

    돌려주는 꼴 — {pipeline_status, total(기록된 급등 수), truncated, anomalies[{id, fired_at, severity, product_code, component,
    symptom, defect_severity, recent_count, ratio(기준 대비 배수), z, indep_sources(독립 출처 수), effective_platforms(옛 탐지기
    값 — 새 기록은 null), baseline_mode(lifecycle | history), reason}], note} — 최근 것부터 limit 건. 같은 급등은 하루에 한
    번까지 다시 기록됩니다.

    Args:
        limit: 건수(1 ~ 100, 기본 20)
        severity: 경보 심각도 — critical(safety · non_functional 결함) | warning (생략하면 전체). 결함 심각도(defect_severity)와 다르다
    """
    return await defect_anomalies_tool(limit, severity)


@mcp.tool()
async def lifecycle_compare(
    product_code: str,
    max_week: Annotated[int, Field(ge=0, le=104)] = 12,
    defect_only: bool = False,
) -> LifecycleCompareOut:
    """제품과 직전 세대를 출시 후 같은 주차(0 주차 = 출시 첫 7일)로 맞춰 주마다 글 수 · 부정 비율 · 결함 비율을 나란히 돌려줍니다.

    신제품의 급등이 진짜 이상인지, 원래 출시 직후 그런 것(출시 효과)인지 가를 때 씁니다. 직전 세대는 카탈로그의 predecessor
    입니다(없으면 그 제품의 주차만 나온다). 달력 월별 추이는 get_product_timeline, 출시 후 며칠에 결함이 터지는지는
    defect_onset, 자동 탐지는 defect_anomalies 를 씁니다. 출시 전 글은 뺍니다.

    돌려주는 꼴 — {pipeline_status, product_code, predecessor_code, weeks[{series(어느 제품의 행), week, total, negative,
    with_defect, negative_pct, defect_pct}], note} — 주차 · 제품 순. 수는 글 단위, 비율은 그 주 글 수 대비(%)이고, 결함
    추출이 안 돈 서버는 with_defect · defect_pct 가 null 입니다. 모르는 코드면 weeks 가 빕니다.

    Args:
        product_code: 제품 코드(예: GS26U)
        max_week: 견줄 마지막 주차(0 ~ 104, 기본 12)
        defect_only: True 면 결함 글이 있는 주차만
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
    interval: Interval = "week",
    period_days: AnyDays = 180,
    product_code: Optional[str] = None,
    component: Optional[Component] = None,
    symptom: Optional[Symptom] = None,
    severity: Optional[Severity] = None,
    modality: Optional[Modality] = None,
    brand: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> DefectTimelineOut:
    """결함 슬라이스(부품 · 증상 · 제품 · 나라 …)의 글 수를 일 · 주 · 월 구간별 시계열로 돌려줍니다 — 언제 시작됐고 가속 중인가.

    숫자 하나로는 '원래 그런 것' 과 '새로 터진 것' 을 가를 수 없을 때 씁니다. 무엇이 문제인지는 defect_profile, 직전 세대와의
    비교는 lifecycle_compare, 부품 · 증상으로 정해지지 않는 낱말의 추이는 keyword_stats, 지난 위기 사례는
    chart_crisis_timeline 을 씁니다. 주는 월요일에 시작하고 기간은 발행일 기준입니다.

    돌려주는 꼴 — {pipeline_status, interval, period_days, filters, first_seen(기간 안의 첫 구간 — 진짜 첫 등장이 아닐 수 있다),
    series[{bucket(구간 시작일), docs, uniq_urls, platforms, firsthand}], note} — 오래된 구간부터, 글이 없는 구간은 빠집니다.
    uniq_urls 가 docs 보다 크게 작으면 같은 글의 페이지 분할이고, firsthand(1인칭 제보 글 수)의 비중이 낮으면 전언 · 기사
    반향입니다.

    Args:
        interval: 구간 — day | week | month (기본 week)
        period_days: 기간 — 최근 N일(발행일 기준, 0 이면 전 기간, 최대 3650, 기본 180). day 구간은 365 이하가 알맞다
    """
    return await defect_timeline_tool(
        interval=interval, period_days=period_days, product_code=product_code,
        component=component, symptom=symptom, severity=severity,
        modality=modality, brand=brand, country=country, platform=platform)


@mcp.tool()
async def defect_breakdown(
    by: DefectAxis = "platform",
    period_days: AnyDays = 90,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    product_code: Optional[str] = None,
    component: Optional[Component] = None,
    symptom: Optional[Symptom] = None,
    severity: Optional[Severity] = None,
    modality: Optional[Modality] = None,
    brand: Optional[str] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> DefectBreakdownOut:
    """결함 슬라이스를 한 축(플랫폼 · 나라 · 양상 · 심각도 · 부품 · 증상 · 제품 · 브랜드 · 기기 종류)으로 나눠 값마다 글 수와 1인칭 제보 비율을 돌려줍니다 — 누가 어디서 말하는가.

    결함이 어느 채널 · 시장에 몰렸는지, 실제 고장 제보인지(firsthand_pct) 볼 때 씁니다. 결함이 아닌 VOC 전체의 분해는
    voc_breakdown, 조합 순위는 defect_profile 을 씁니다. firsthand_pct 가 낮으면 전언 · 기사 반향이라 실제 고장률로 읽으면
    안 됩니다. 기간은 발행일 기준입니다.

    돌려주는 꼴 — {pipeline_status, by, period_days, filters, total(축 값 수), truncated, rows[{key, docs, firsthand(1인칭 제보 글
    수), firsthand_pct}]} — 글이 많은 값부터 limit 개. 값이 비면 key 는 unknown(플랫폼 · 양상) · ??(나라)입니다.

    Args:
        by: 축 — platform | country | modality | severity | component | symptom | product | brand | category(기기 종류) (기본 platform)
        period_days: 기간 — 최근 N일(발행일 기준, 0 이면 전 기간, 최대 3650, 기본 90)
        limit: 축 값 수(1 ~ 100, 기본 20)
    """
    return await defect_breakdown_tool(
        by=by, period_days=period_days, limit=limit, product_code=product_code,
        component=component, symptom=symptom, severity=severity,
        modality=modality, brand=brand, country=country, platform=platform)


@mcp.tool()
async def defect_cooccurrence(
    period_days: AnyDays = 180,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    min_docs: Annotated[int, Field(ge=2, le=1000)] = 3,
    product_code: Optional[str] = None,
    component: Optional[Component] = None,
    brand: Optional[str] = None,
    country: Optional[str] = None,
) -> DefectCooccurrenceOut:
    """같은 글에 함께 나온 결함 쌍(부품/증상 + 부품/증상)을 함께 나온 글 수 순서로 돌려줍니다 — 복합 고장 모드 찾기.

    '배터리 광탈과 발열이 같이 나오나' 처럼 단일 증상 집계로는 안 보이는 묶음을 볼 때 씁니다. 동시 등장이지 인과가 아닙니다
    (한 글이 불만을 여럿 늘어놓았을 수 있음). 낱말끼리의 관계망은 chart_keyword_network, 결함 순위는 defect_profile 을
    씁니다. component 를 주면 쌍의 양쪽이 모두 그 부품인 것만 남습니다(그 부품이 낀 쌍이 아님). 모든 양상을 셉니다.

    돌려주는 꼴 — {pipeline_status, period_days, filters, total(min_docs 를 넘은 쌍 수), truncated, pairs[{defect_a, defect_b,
    co_docs}], note} — defect_a 가 사전순 앞입니다.

    Args:
        period_days: 기간 — 최근 N일(발행일 기준, 0 이면 전 기간, 최대 3650, 기본 180)
        limit: 쌍 수(1 ~ 100, 기본 20)
        min_docs: 쌍으로 칠 최소 글 수(2 ~ 1000, 기본 3) — 작은 우연을 거른다
    """
    return await defect_cooccurrence_tool(
        period_days=period_days, limit=limit, min_docs=min_docs,
        product_code=product_code, component=component, brand=brand, country=country)


@mcp.tool()
async def defect_onset(
    period_days: AnyDays = 730,
    product_code: Optional[str] = None,
    component: Optional[Component] = None,
    symptom: Optional[Symptom] = None,
    severity: Optional[Severity] = None,
    brand: Optional[str] = None,
) -> DefectOnsetOut:
    """결함 제보가 출시 후 며칠에 나오는지의 분포(평균 · 사분위 · 30일 안 · 1년 뒤)를 돌려줍니다 — 초기 불량과 마모 고장 가르기.

    median_days 가 작고 within_30d 가 크면 초기 불량, after_1y 가 크면 마모 · 열화 패턴입니다. 출시일이 있는 제품과 출시 뒤
    글만 셉니다. 제품을 생략하면 제품마다 자기 출시일에서 잰 값을 모읍니다. 출시 후 주차별 비교는 lifecycle_compare, 달력
    시간 추이는 defect_timeline 을 씁니다. 수집을 시작하기 전에 나온 제품은 초기 구간이 비어 median 이 실제보다 큽니다.

    돌려주는 꼴 — {pipeline_status, docs, avg_days, p25_days, median_days, p75_days, within_30d, after_1y, period_days, filters,
    note} — 한 행. 단위는 (글, 제품) 쌍이라 비교글은 제품마다 셉니다. 글이 없으면 docs 0 과 null 입니다.

    Args:
        period_days: 기간 — 최근 N일(발행일 기준 — 출시 후 경과일이 아님, 0 이면 전 기간, 최대 3650, 기본 730)
    """
    return await defect_onset_tool(
        period_days=period_days, product_code=product_code, component=component,
        symptom=symptom, severity=severity, brand=brand)


@mcp.tool()
async def defect_evidence(
    period_days: AnyDays = 180,
    limit: Annotated[int, Field(ge=1, le=50)] = 10,
    modality: Optional[Modality] = "firsthand",
    product_code: Optional[str] = None,
    component: Optional[Component] = None,
    symptom: Optional[Symptom] = None,
    severity: Optional[Severity] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> dict:
    """결함 슬라이스의 실제 사용자 발언(원문 400자)을 최근 글부터 돌려줍니다 — 시나리오를 뒷받침하는 원문 근거.

    부품 · 증상 · 양상이 정해진 결함의 인용이 필요할 때 씁니다. 기본은 1인칭 제보(firsthand — 자기 기기에서 겪은 고장)만
    봅니다. 관련도나 참여도 순서, 결함으로 정해지지 않는 주제는 search_voc, 필터 목록은 query_voc 를 씁니다. 같은 글의
    페이지 분할은 URL(쿼리 뺀 것)로 한 건으로 접습니다.

    돌려주는 꼴 — {pipeline_status, period_days, filters, total(접은 글 수), truncated, samples[{id, source_url, published_at,
    country_code, platform, product_code, component, symptom, severity, modality, sentiment_label, snippet}], note} — 최근 글부터
    limit 건. 한 글에 결함 · 제품이 여럿이면 그중 하나를 보입니다. 작성자 · 계정은 가려서 나갑니다.

    Args:
        period_days: 기간 — 최근 N일(발행일 기준, 0 이면 전 기간, 최대 3650, 기본 180)
        limit: 글 수(1 ~ 50, 기본 10)
        modality: 글의 양상 — firsthand(기본, 본인이 실제 겪은 고장) | worry | question | hearsay | review | other, null 이면 전체
    """
    return await defect_evidence_tool(
        period_days=period_days, limit=limit, modality=modality,
        product_code=product_code, component=component, symptom=symptom,
        severity=severity, country=country, platform=platform)


# ── 전체 통계 ──────────────────────────────────────────────────────────────
# 행만 주는 도구로는 "총 몇 건인지, 전기 대비 얼마나 늘었는지, 어느 제품에 몰렸는지" 를 말할 수 없다.
# 아래 넷은 답변의 전제(규모 · 증감 · 분포)를 만든다 — SQL 은 shared/stats_sql.py(REST 와 같은 것).
@mcp.tool()
async def corpus_overview(days: Optional[AnyDays] = None) -> CorpusOverviewOut:
    """SignalForge 를 처음 부를 때 쓰는 입구 — 말뭉치 전체 현황(규모 · 기간 · 플랫폼 · 나라 · 언어 · 제품 태깅 · 품질 · 결함률)과 대표 업무의 길 안내를 한 번에 돌려줍니다.

    어떤 분석이든 답의 전제(무엇을 얼마나 갖고 있나)를 먼저 세울 때 씁니다. 결과의 guide 가 업무별로 다음에 부를 도구의
    차례를 줍니다 — 제품 결함 조사 · 증상 찾기 · 급등 감시 · 경쟁 비교 · PLM 과제로 찾기. 제품이 안 붙은 글의 사유는
    get_unmapped_voc, 수집 신선도는 site_health, 하루치 글 요약은 daily_briefing, 분포는 voc_breakdown 을 씁니다.

    돌려주는 꼴 — {period_days, scale{voc_total, oldest(1퍼센타일 발행일), oldest_raw, newest, platforms, countries, languages},
    coverage{tagged, tagged_pct, products_seen, catalog_products, catalog_active, catalog_by_category}, quality{categorized_pct,
    negative_pct}, defects{pipeline_status, docs, records, firsthand, defect_rate_pct}, note, guide{workflows[{purpose, entry,
    steps}], rules}}. 기간은 발행일 기준이고 카탈로그 수는 기간과 무관합니다. 결함 추출이 안 돈 서버는 defect_rate_pct 가
    0 이 아니라 null 입니다.

    Args:
        days: 최근 N일(발행일 기준, 최대 3650)로 한정 — 생략하거나 0 이면 전 기간
    """
    return {**await corpus_overview_tool(days), "guide": _GUIDE}


@mcp.tool()
async def keyword_stats(
    keyword: str,
    days: Days = 90,
    interval: Interval = "week",
    top_n: Annotated[int, Field(ge=1, le=50)] = 8,
    product_code: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[DeviceCategory] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
) -> KeywordStatsOut:
    """낱말 하나의 통계 — 최근 N일 건수 · 고유 URL · 플랫폼 · 나라 수 · 부정 비율 · 직전 N일 대비 증감 · 구간별 추이 · 제품 · 플랫폼 · 나라별 상위와 비중을 한 번에 돌려줍니다.

    '발열 얘기가 얼마나 되고 늘고 있나, 어디에 몰렸나' 처럼 낱말의 규모 · 변화를 말할 때 씁니다(search_voc 가 글을 준다면
    이것은 통계를 줍니다). 낱말은 글자 그대로 맞춥니다 — 한국어는 원문 부분일치, 영어 한 낱말은 번역문 전문검색(어간), 여러
    낱말은 구절. 동의어로 넓히지 않으니 뜻 검색 건수(search_voc 의 total)와 다를 수 있습니다. 부품 · 증상으로 정해지는 결함은
    defect_timeline · defect_profile, 축별 증감은 period_compare(keyword=…), 축 분해는 voc_breakdown(keyword=…)을 씁니다.

    돌려주는 꼴 — {keyword, days, match(substring | phrase | fts), filters, total{docs, uniq_urls, platforms, countries, negative,
    negative_pct, avg_sentiment, first_seen, last_seen}, vs_previous_period{prev_docs, delta_pct(직전이 0 이면 null)},
    series[{bucket, docs}](글이 없는 구간은 빠짐), top{product · platform · country: [{key, docs, share_pct}]}(각 top_n 개),
    note}. 기간은 발행일 기준입니다.

    Args:
        keyword: 셀 낱말(한국어 가능 — 글자 그대로)
        days: 기간 — 최근 N일(1 ~ 3650, 기본 90). 증감은 그 앞 N일과 견준다
        interval: 추이 구간 — day | week | month (기본 week)
        top_n: 제품 · 플랫폼 · 나라 상위 수(1 ~ 50, 기본 8)
    """
    return await keyword_stats_tool(
        keyword, days=days, interval=interval, top_n=top_n,
        product_code=product_code, brand=brand, category=category,
        country=country, platform=platform)


@mcp.tool()
async def period_compare(
    days: Days = 30,
    by: CompareAxis = "product",
    limit: Annotated[int, Field(ge=1, le=100)] = 15,
    keyword: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[DeviceCategory] = None,
    country: Optional[str] = None,
) -> PeriodCompareOut:
    """최근 N일과 그 앞 N일의 VOC 건수를 축(제품 · 브랜드 · 기기 종류 · 나라 · 플랫폼) 값마다 견줘, 늘고 준 것을 돌려줍니다.

    '지난달보다 무엇이 늘었나' 를 볼 때 씁니다. 낱말 하나의 증감은 keyword_stats, 정적인 분포는 voc_breakdown, 출시 효과를
    걷어낸 결함 급등은 defect_anomalies · lifecycle_compare, 지난 24시간 경보는 alert_check 를 씁니다. 출시 직후의 급등도
    그대로 늘어난 것으로 나옵니다. 기간은 발행일 기준이고 수집 깊이가 기간마다 다를 수 있어 절대 증감과 함께 봅니다.

    돌려주는 꼴 — {by, days, keyword, match, filters, rows[{key, current, previous, delta, delta_pct(직전이 0 이면 null)}],
    groups(축 값 전체 수), truncated, risers · fallers(각 최대 5), note}. rows 는 증감 절댓값이 큰 순서로 limit 개이고, risers ·
    fallers 는 그 rows 안에서 고릅니다.

    Args:
        days: 견줄 기간의 길이 N — 최근 N일 vs 그 앞 N일(1 ~ 3650, 기본 30)
        by: 축 — product | brand | category(기기 종류) | country | platform (기본 product)
        limit: rows 수(1 ~ 100, 기본 15)
        keyword: 낱말로 슬라이스 한정(글자 그대로, 한국어 가능) — 선택
    """
    return await period_compare_tool(
        days=days, by=by, limit=limit, keyword=keyword,
        brand=brand, category=category, country=country)


@mcp.tool()
async def voc_breakdown(
    by: VocAxis = "product",
    days: Optional[AnyDays] = 30,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
    keyword: Optional[str] = None,
    product_code: Optional[str] = None,
    brand: Optional[str] = None,
    category: Optional[DeviceCategory] = None,
    country: Optional[str] = None,
    platform: Optional[str] = None,
    sentiment: Optional[Sentiment] = None,
    include_mentions: bool = False,
) -> VocBreakdownOut:
    """VOC 전체(결함에 한정하지 않음)를 한 축으로 나눠 값마다 건수 · 부정 건수 · 부정 비율 · 평균 감성 · 비중을 돌려줍니다.

    '어느 제품 · 나라 · 플랫폼 · 언어에 말이 몰렸나' 와 그 비중을 인용할 때 씁니다. 결함만은 defect_breakdown(1인칭 비중
    포함), 기간 대비 증감은 period_compare, 낱말 하나의 프로파일은 keyword_stats, 주제(배터리 · 카메라 …)별은
    chart_category_distribution · get_top_issues 를 씁니다 — 여기의 category 축은 기기 종류(phone · watch …)입니다. 기간은
    발행일 기준입니다.

    돌려주는 꼴 — {by, days, keyword, match, include_mentions, total(슬라이스 전체 글 수), groups(축 값 전체 수), truncated,
    rows[{key, docs, negative, negative_pct, avg_sentiment, share_pct}], note} — 많은 값부터 limit 개. 값이 비면 key 는
    (untagged)(제품 · 브랜드 · 기기 종류) · ??(나라) · unknown(플랫폼 · 감성) · ?(언어)입니다.

    Args:
        by: 축 — product | brand | category(기기 종류) | country | platform | platform_kind(media · community · marketplace …) |
            language | sentiment (기본 product)
        days: 최근 N일(발행일 기준, 최대 3650, 기본 30) — 0 이나 null 이면 전 기간
        limit: rows 수(1 ~ 100, 기본 20)
        keyword: 낱말로 슬라이스 한정(글자 그대로, 한국어 가능) — 선택
        include_mentions: 비교글에서 언급만 된 제품까지 셉니다(기본 False — 대표 제품만). 켜면 한 글이 제품 수만큼 세어져
            total 이 링크 수가 되고, 신제품처럼 비교 대상으로만 나오는 제품이 잡힙니다. 경쟁 비교 · 신제품 화제성을 볼 때 켭니다
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
