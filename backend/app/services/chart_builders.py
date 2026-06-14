"""차트 규격 빌더 — ECharts option 을 frontend chartTheme.ts 규격으로 생성.

backend /charts/* router 와 MCP charts.py 가 공유하는 빌더의 backend 사본.
DB 무관 순수 함수. (MCP 는 sys.path 분리로 직접 import 불가 → 동일 코드 사본.
색 8종은 frontend chartTheme.ts ↔ mcp-server/tools/chart_spec.py ↔ 이 파일 3곳 동기화.)

⚠️ 색 동기화: PALETTE / SERIES_COLORS 는 frontend/src/utils/chartTheme.ts 의
   palette / seriesColors 와 1:1 동일해야 한다 (Okabe-Ito 색맹 친화 8색).
   한쪽을 바꾸면 반드시 다른 쪽도 바꿀 것. grep "0072B2" 로 양쪽 추적 가능.

⚠️ tooltip formatter 제약: ECharts 의 한국어 단위 formatter 는 JS 함수라
   JSON 직렬화가 불가능하다. 따라서 여기서는 tooltip.trigger 만 지정하고
   formatter 는 생략한다. frontend 가 이 option 을 렌더할 때 자체 formatter
   (chartTheme.ts 의 defaultAxisTooltipFormatter 등) 를 주입하면 된다.
"""
from __future__ import annotations
from typing import Any, Optional

# chartTheme.ts palette 와 1:1 (동기화 의무)
PALETTE = {
    "primary": "#0072B2",
    "accent": "#E69F00",
    "positive": "#009E73",
    "negative": "#D55E00",
    "warning": "#F0E442",
    "info": "#56B4E9",
    "neutral": "#999999",
    "secondary": "#CC79A7",
}

# chartTheme.ts seriesColors 회전 순서와 동일
SERIES_COLORS = [
    PALETTE["primary"], PALETTE["accent"], PALETTE["positive"],
    PALETTE["negative"], PALETTE["info"], PALETTE["secondary"],
    PALETTE["warning"], PALETTE["neutral"],
]


def _base(title: str) -> dict:
    """모든 차트 공통 골격 (title + tooltip.trigger + grid)."""
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
        "color": SERIES_COLORS,
    }


def build_line(labels: list[str], series: list[dict], title: str,
               *, value_unit: str = "건") -> dict:
    """다중 시리즈 라인 차트. series=[{"name": str, "data": [num,...]}]."""
    opt = _base(title)
    opt["xAxis"] = {"type": "category", "data": labels, "boundaryGap": False}
    opt["yAxis"] = {"type": "value", "name": value_unit}
    opt["legend"] = {"data": [s["name"] for s in series], "top": 30}
    opt["series"] = [
        {"name": s["name"], "type": "line", "smooth": True,
         "showSymbol": len(labels) <= 30, "data": s["data"]}
        for s in series
    ]
    return opt


def build_bar(labels: list[str], values: list[float], title: str,
              *, horizontal: bool = False, value_unit: str = "건") -> dict:
    """단일 시리즈 막대. horizontal=True 면 가로 막대 (top-N 분포에 적합)."""
    opt = _base(title)
    opt["tooltip"] = {"trigger": "item"}
    cat_axis = {"type": "category", "data": labels}
    val_axis = {"type": "value", "name": value_unit}
    if horizontal:
        opt["xAxis"], opt["yAxis"] = val_axis, {**cat_axis, "inverse": True}
    else:
        opt["xAxis"], opt["yAxis"] = cat_axis, val_axis
    opt["series"] = [{"type": "bar", "data": values}]
    return opt


def build_pie(labels: list[str], values: list[float], title: str) -> dict:
    """파이/도넛 차트 (구성비)."""
    opt = _base(title)
    opt["tooltip"] = {"trigger": "item"}
    opt.pop("grid", None)
    opt["series"] = [{
        "type": "pie", "radius": ["35%", "65%"],
        "data": [{"name": n, "value": v} for n, v in zip(labels, values)],
    }]
    return opt


def build_timeline(labels: list[str], values: list[float], title: str,
                   *, markers: Optional[list[dict]] = None,
                   value_unit: str = "건") -> dict:
    """이벤트 타임라인 (line+area). markers=[{"coord":[x,y],"name":...}] 스파이크 표시."""
    opt = _base(title)
    opt["xAxis"] = {"type": "category", "data": labels, "boundaryGap": False}
    opt["yAxis"] = {"type": "value", "name": value_unit}
    s: dict[str, Any] = {
        "type": "line", "smooth": False, "areaStyle": {"opacity": 0.2},
        "data": values, "showSymbol": len(labels) <= 60,
    }
    if markers:
        s["markPoint"] = {"data": markers}
    opt["series"] = [s]
    return opt


def build_graph(nodes: list[dict], edges: list[dict], title: str) -> dict:
    """force-directed 네트워크. nodes=[{id,name,value,category}], edges=[{source,target,value}].

    category (community_id) 가 있으면 categories 로 색 분리, value 로 symbolSize.
    """
    cats = sorted({n.get("category", 0) for n in nodes})
    cat_index = {c: i for i, c in enumerate(cats)}
    return {
        "title": {"text": title, "left": "center"},
        "tooltip": {},
        "color": SERIES_COLORS,
        "legend": [{"data": [f"군집 {c}" for c in cats], "top": 30}] if len(cats) > 1 else [],
        "series": [{
            "type": "graph", "layout": "force", "roam": True,
            "label": {"show": True, "position": "right"},
            "force": {"repulsion": 120, "edgeLength": [40, 120]},
            "categories": [{"name": f"군집 {c}"} for c in cats],
            "data": [{
                "id": str(n["id"]), "name": n.get("name", str(n["id"])),
                "value": n.get("value", 1),
                "symbolSize": min(8 + (n.get("value", 1) ** 0.5) * 4, 50),
                "category": cat_index.get(n.get("category", 0), 0),
            } for n in nodes],
            "links": [{"source": str(e["source"]), "target": str(e["target"]),
                       "value": e.get("value", 1)} for e in edges],
        }],
    }


def chart_response(chart_type: str, raw: Any, echarts_option: dict,
                   summary: str) -> dict:
    """모든 차트 도구의 표준 반환 래퍼.

    LLM 은 echarts_option 을 그대로 ReactECharts/렌더에 넘기거나, raw 로
    다른 라이브러리 (Vega/matplotlib) 에 매핑할 수 있다.
    """
    return {
        "chart_type": chart_type,
        "raw": raw,
        "echarts_option": echarts_option,
        "summary": summary,
    }
