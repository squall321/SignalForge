"""P4.2 E5 — Alerts 룰 프리셋 5종.

운영자가 한 번 클릭으로 자주 쓰는 룰을 등록할 수 있도록 정의한 정적 카탈로그.
DB seed (alembic 0005) 와는 별개의 "옵션 메뉴" 로 동작 — apply 호출 시 중복 이름 skip.

값 (metric_path/op/threshold) 는 collect_metrics 가 산출하는 키와 1:1 일치해야 한다.
"""
from __future__ import annotations

from typing import Any, Dict, List


DEFAULT_PRESETS: List[Dict[str, Any]] = [
    {
        "key": "high_burst_negative",
        "name": "부정 급증",
        "metric_path": "community.extreme_negative_count",
        "op": ">",
        "threshold": 5,
        "severity": "critical",
        "cooldown_sec": 1800,
        "description": "평가 시점 7일 평균 감성이 -0.3 이하인 플랫폼이 5곳 초과",
    },
    {
        "key": "new_term_storm",
        "name": "신조어 폭증",
        "metric_path": "insights.new_term_spike_count",
        "op": ">=",
        "threshold": 100,
        "severity": "warning",
        "cooldown_sec": 900,
        "description": "7일 신조어 spike 100개 이상 — 사회적 화제 가능성",
    },
    {
        "key": "negative_rate_severe",
        "name": "부정율 한계",
        "metric_path": "community.negative_rate_max",
        "op": ">",
        "threshold": 0.6,
        "severity": "critical",
        "cooldown_sec": 1800,
        "description": "특정 플랫폼 부정율 60% 초과",
    },
    {
        "key": "new_term_warning",
        "name": "신조어 주의",
        "metric_path": "insights.new_term_spike_count",
        "op": ">=",
        "threshold": 50,
        "severity": "info",
        "cooldown_sec": 3600,
        "description": "신조어 50개 이상 — 일상 모니터링",
    },
    {
        "key": "extreme_neg_singular",
        "name": "단일 플랫폼 위기",
        "metric_path": "community.extreme_negative_count",
        "op": ">=",
        "threshold": 1,
        "severity": "info",
        "cooldown_sec": 7200,
        "description": "플랫폼 1곳이라도 극단 부정 진입",
    },
]


PRESETS_BY_KEY: Dict[str, Dict[str, Any]] = {p["key"]: p for p in DEFAULT_PRESETS}


__all__ = ["DEFAULT_PRESETS", "PRESETS_BY_KEY"]
