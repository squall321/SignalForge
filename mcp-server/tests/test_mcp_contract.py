# MCP 공통 계약(포털 docs/mcp-modernize/contract.md)의 stateless · 인자 설명 · 읽기 표지를 tools/list JSON 으로 고정한다
"""왜 이 시험인가 (2026-11-28).

게이트웨이에는 앱 도구가 2,000개 가까이 모이고 모델은 이름 · 설명 · 인자 스키마로만 고른다. 인자 설명이 산문 「Args:」 에만
있으면 스키마에는 빈칸이고, 읽기 표지가 없으면 읽기 도구를 쓰기로 막는다. SDK 1.x 의 표지 칸은 camelCase 라 read_only_hint=
로 쓰면 예외도 경고도 없이 모르는 칸으로 나간다 — 파이썬 속성이 아니라 tools/list 로 나가는 JSON 의 열쇠를 본다.
"""
import asyncio
import pathlib
import re
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
pytest.importorskip("mcp", reason="mcp SDK 가 없는 환경")


@pytest.fixture(scope="module")
def server():
    import server as srv          # conftest 가 DATABASE_URL 을 정한다 — 접속은 하지 않는다
    return srv


@pytest.fixture(scope="module")
def tools(server):
    listed = asyncio.run(server.mcp.list_tools())
    return {t.name: t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listed}


def test_server_is_stateless(server):
    assert server.mcp.settings.stateless_http is True


def test_every_tool_carries_camel_case_read_only_annotations(tools):
    assert len(tools) >= 37
    for name, t in tools.items():
        ann = t.get("annotations") or {}
        assert ann.get("readOnlyHint") is True and ann.get("openWorldHint") is False, (name, ann)
        assert not any("_" in k for k in ann), f"{name}: 표지 열쇠가 camelCase 가 아니다 {sorted(ann)}"


def test_every_argument_has_a_schema_description_and_no_prose_args(tools):
    missing = [(n, k) for n, t in tools.items() for k, v in t["inputSchema"].get("properties", {}).items()
               if not v.get("description")]
    assert not missing, missing
    assert not [n for n, t in tools.items() if "Args:" in t["description"]], "설명에 산문 Args 절이 남았다"


def test_args_parser_keeps_continuations_and_skips_shorthand_lines(server):
    doc = """한 줄 설명.

    Args:
        interval: day | week | month
            (기본 week)
        나머지: 공통 필터
        limit: 상위 N개

    반환에 total 이 있다.
    """
    desc, args = server._split_args(doc)
    assert args == {"interval": "day | week | month (기본 week)", "limit": "상위 N개"}
    assert desc == "한 줄 설명.\n\n반환에 total 이 있다."


# ── 2단계(2026-11-28) — 설명의 꼴 · 닫힌 어휘 · 숫자 경계 · 결과 모델 · 정직한 한도 · 바뀐 것 목록 · 앱 입구 ──────────
def _leaf(prop: dict) -> dict:
    """Optional[...] 의 anyOf 에서 null 이 아닌 쪽."""
    return next((x for x in prop.get("anyOf", [prop]) if x.get("type") != "null"), prop)


_HISTORY = re.compile(r"20\d\d-\d\d|실측|SG-\d+|SFO-\d+|HP-\d+")


def test_descriptions_follow_contract_form(tools):
    """C1 — 날짜 · 결정 번호 · 실측 일화가 없고 1,500자 안이며, 형제 도구를 이름으로 짚는다."""
    for name, t in tools.items():
        d = t["description"]
        assert len(d) <= 1500, (name, len(d))
        hit = _HISTORY.search(d)
        assert not hit, (name, hit and hit.group())
        assert (set(tools) - {name}) & set(re.findall(r"[a-z_]+", d)), f"{name}: 형제 도구 이름이 없다"


def test_closed_vocabularies_are_enums(tools):
    """C3 — 정해진 낱말 집합은 enum 이다. 모르는 값은 예전처럼 다른 축 · 기본값으로 조용히 바뀌지 않고 거절된다."""
    want = [("voc_breakdown", "by"), ("period_compare", "by"), ("defect_breakdown", "by"), ("defect_timeline", "interval"),
            ("keyword_stats", "interval"), ("analyze_sentiment_trend", "granularity"), ("search_voc", "order"),
            ("search_voc", "match"), ("search_voc", "core_type"), ("semantic_search", "order"), ("query_voc", "category"),
            ("query_voc", "sentiment"), ("compare_products", "category"), ("defect_profile", "component"),
            ("defect_profile", "severity"), ("defect_timeline", "symptom"), ("defect_evidence", "modality"),
            ("defect_anomalies", "severity"), ("get_kg_relations", "edge_type"), ("get_unmapped_voc", "reason"),
            ("keyword_stats", "category"), ("list_changes", "events"), ("chart_crisis_timeline", "case_code")]
    missing = [(n, a) for n, a in want
               if "enum" not in (lambda leaf: leaf.get("items", leaf))(_leaf(tools[n]["inputSchema"]["properties"][a]))]
    assert not missing, missing
    topics = _leaf(tools["query_voc"]["inputSchema"]["properties"]["category"])["enum"]
    assert {"battery", "review", "model_mention"} <= set(topics) and "phone" not in topics, "VOC 주제가 기기 종류와 섞였다"


def test_integer_arguments_are_bounded(tools):
    """C4 — 개수 · 기간 정수에는 최소 · 최대가 있다(함수 안에서 조용히 자르지 않는다). 커서(since)만 위가 열려 있다."""
    loose = [(n, k) for n, t in tools.items() for k, v in t["inputSchema"].get("properties", {}).items()
             if _leaf(v).get("type") == "integer" and k != "since"
             and not ("minimum" in _leaf(v) and "maximum" in _leaf(v))]
    assert not loose, loose


# 큰 글을 내는 도구는 게이트웨이가 한쪽만 넘길 때까지(포털 PLAN M2) 결과 모델을 달지 않는다. compare_products 는 열쇠가
# 제품 코드라(동적) 칸으로 적을 것이 없다
_UNMODELLED = {"query_voc", "search_voc", "semantic_search", "get_ontology", "defect_evidence", "compare_products"}


def test_structured_tools_publish_output_schema_with_fields(tools):
    """C7 — 나머지 도구는 실제 칸이 있는 outputSchema 를 낸다(글만 내는 도구는 SDK 의 {result} 껍데기)."""
    for name, t in tools.items():
        props = (t.get("outputSchema") or {}).get("properties", {})
        assert bool(props) != (name in _UNMODELLED), (name, sorted(props))


def test_list_tools_say_when_they_cut(server, monkeypatch):
    """C8 — 목록 도구는 잘랐으면 잘랐다고 한다. 집계는 창 함수로 센 전체 수, 행 도구는 한 건 더 읽은 것으로 안다."""
    async def platforms(product_code, period_days, top_n):
        return [{"platform_code": f"p{i}", "voc_count": 9 - i, "total_rows": 7} for i in range(min(top_n, 7))]
    monkeypatch.setattr(server, "get_platform_breakdown_tool", platforms)
    out = asyncio.run(server.get_platform_breakdown(top_n=3))
    assert (out["total"], out["truncated"], len(out["results"])) == (7, True, 3)
    assert "total_rows" not in out["results"][0]

    async def leaders(product_code, period_days, limit):
        return [{"id": i} for i in range(min(limit, 4))]            # 글이 넷뿐
    monkeypatch.setattr(server, "get_engagement_leaders_tool", leaders)
    cut = asyncio.run(server.get_engagement_leaders(limit=3))
    assert (cut["total"], cut["truncated"], len(cut["results"])) == (None, True, 3)
    whole = asyncio.run(server.get_engagement_leaders(limit=10))
    assert (whole["total"], whole["truncated"]) == (4, False)

    async def voc_rows(*a):
        limit, offset = a[8], a[9]
        return [{"id": i} for i in range(5)][offset:offset + limit]  # 행이 다섯
    monkeypatch.setattr(server, "query_voc_tool", voc_rows)
    first = asyncio.run(server.query_voc(limit=2))
    assert (first["truncated"], first["next_cursor"], first["total"]) == (True, "2", None)
    last = asyncio.run(server.query_voc(limit=2, cursor="4"))
    assert (last["truncated"], last["next_cursor"], last["total"], last["results"]) == (False, None, 5, [{"id": 4}])


class _Rows:
    def __init__(self, rows=None, scalar=None):
        self.rows, self._scalar = rows or [], scalar

    def mappings(self):
        return self

    def all(self):
        return self.rows

    def scalar(self):
        return self._scalar


class _AlertsDB:
    """alert_events 흉내 — since · 차례 · 한 건 더 읽기를 SQL 과 인자로 따른다."""

    def __init__(self, events):
        self.events = events

    async def execute(self, stmt, params=None):
        sql, params = str(stmt), params or {}
        rows = [e for e in self.events if e["id"] > params.get("since", -1)]
        if "count(*)" in sql:
            return _Rows(scalar=len(rows))
        rows = sorted(rows, key=lambda e: e["id"], reverse="e.id DESC" in sql)
        return _Rows(rows=[dict(r) for r in rows[:params["lim"]]])


def test_list_changes_continues_by_id_cursor_and_names_events(monkeypatch):
    """R1 — since 뒤를 오래된 것부터 주고 next_cursor 로 빠짐 · 겹침 없이 이어진다. 이름은 signalforge.<대상>.<일>."""
    import contextlib
    from tools import changes as C

    events = [{"id": i, "rule": "defect_anomaly" if i % 2 else "platforms_negative_share", "severity": "warning",
               "fired_at": None, "value": 1.0, "threshold": 0.5,
               "payload": {"type": "defect_anomaly", "reason": f"급등 {i}"} if i % 2 else {"description": "규칙"}}
              for i in range(1, 8)]

    @contextlib.asynccontextmanager
    async def session():
        yield _AlertsDB(events)
    monkeypatch.setattr(C, "get_db_session", session)

    latest = asyncio.run(C.list_changes_tool(limit=3))
    assert [e["id"] for e in latest["events"]] == [5, 6, 7] and latest["next_cursor"] == 7 and latest["truncated"]
    seen, cursor = [], 0
    while True:
        page = asyncio.run(C.list_changes_tool(since=cursor, limit=3))
        seen += [e["id"] for e in page["events"]]
        cursor = page["next_cursor"]
        if not page["truncated"]:
            break
    assert seen == list(range(1, 8)) and cursor == 7
    assert asyncio.run(C.list_changes_tool(since=7))["events"] == []
    assert asyncio.run(C.list_changes_tool(since=7))["next_cursor"] == 7      # 새 일이 없으면 받은 커서 그대로
    names = {e["event"] for e in asyncio.run(C.list_changes_tool(since=0, limit=10))["events"]}
    assert names == {"signalforge.defect.spiked", "signalforge.alert.fired"}
    assert all(re.fullmatch(r"signalforge\.[a-z_]+\.[a-z_]+", n) for n in [*C.EVENT_NAMES.values(), C.RULE_EVENT])


def test_entry_tool_is_corpus_overview_with_guide(server, tools):
    """R3 — 입구는 corpus_overview(서버 instructions 가 짚는다), 결과의 guide 는 이 서버에 있는 도구만 차례로 든다."""
    assert "corpus_overview" in server.mcp.instructions
    assert "guide" in tools["corpus_overview"]["outputSchema"]["properties"]
    for wf in server._GUIDE["workflows"]:
        assert wf["entry"] in tools and wf["entry"] == wf["steps"][0], wf
        assert set(wf["steps"]) <= set(tools), wf


def _call(server, name, args):
    """FastMCP 1.x 의 call_tool — 결과 모델이 있으면 (글, 구조), 없으면 글뿐."""
    r = asyncio.run(server.mcp.call_tool(name, args))
    return r if isinstance(r, tuple) else (r, None)


def test_unknown_arguments_are_reported_not_dropped_silently(server, monkeypatch, caplog):
    """SDK 1.x 는 도구에 없는 인자를 오류 없이 버린다 — 리스크 앱이 get_top_issues 에 window_days=90 을 넘겨 기본 30일로 돌았다.
    거절하지 않고 결과의 ignored_args 와 서버 경고 로그로 드러낸다(포털 71 11-28 — 거절은 부르는 쪽이 고친 뒤에 다시 정한다)."""
    import json
    import logging

    async def top(product_code, period_days, top_n):
        return [{"category": "camera", "total_count": 3, "negative_rate": 10.0, "total_rows": 1}]
    monkeypatch.setattr(server, "get_top_issues_tool", top)
    with caplog.at_level(logging.WARNING, logger="signalforge.mcp"):
        content, structured = _call(server, "get_top_issues", {"product_code": "GS25U", "window_days": 90})
    assert structured["ignored_args"] == ["window_days"] and structured["period_days"] == 30
    assert json.loads(content[0].text)["ignored_args"] == ["window_days"]
    warned = [r.getMessage() for r in caplog.records if r.name == "signalforge.mcp"]
    assert warned and "get_top_issues" in warned[0] and "window_days" in warned[0] and "90" not in warned[0]

    content, structured = _call(server, "get_top_issues", {"product_code": "GS25U"})
    assert "ignored_args" not in json.loads(content[0].text) and structured["ignored_args"] is None

    async def summary(product_code, period_days):
        return "요약"
    monkeypatch.setattr(server, "get_voc_summary_tool", summary)
    content, _ = _call(server, "get_voc_summary", {"product_code": "GS25U", "days": 3})
    assert content[0].text.startswith("요약") and content[0].text.rstrip().endswith("days)")

    async def compare(codes, category):
        return {"GS25U": {"battery": 0.1}}
    monkeypatch.setattr(server, "compare_products_tool", compare)
    content, _ = _call(server, "compare_products", {"product_codes": ["GS25U"], "period_days": 7})
    assert json.loads(content[0].text) == {"GS25U": {"battery": 0.1}}, "열쇠가 제품 코드인 결과에는 칸을 섞지 않는다"
