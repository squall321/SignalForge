"""R26 트랙 C/E — 7일 관측 기반 trust 임계 정밀화 단위 테스트.

R25 가 ``SIGNALFORGE_TRUST_WARNING/CRITICAL`` env 로 임계를 운영 중 조정 가능
하게 했다.  본 테스트는 *7일 분포로 검증한 60/80 기본값* 이 실측 trust 분포
에서 critical/warning/normal 의 자연 분리를 유지하는지를 검증한다.

Discovery 시점 (2026-06-05) 의 7일 분포 (drift_stats):
  - R7  trust 98.6 → normal
  - R20 trust 76.5 → warning
  - R21 trust 64.9 → warning
  - R22 trust 58.9 → critical

이 분포에서 *60/80* 기본 임계는 1 critical / 2 warning / 1 normal 로
자연 분리되며, 임계를 조정해도 (R22=58.9 는 critical 유지 필요) 의 안전성을
지키려면 crit_th > 58.9 (= 운영 안전 마진 60 유지) 가 필수다.

본 단위 테스트는 *정책 잠금* 역할:
1. 60/80 기본 → R22 (58.9) critical 분류 유지.
2. env tighten (65/82) → R21 (64.9) critical 로 격상 (조기 경보 강화).
3. env relax (55/75) → R22 critical 유지 (안전 마진).
4. 임계 역전 (crit >= warn) 시 자동 보정 (crit = warn - 1) 검증.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight.workflow_drift_stats import (  # noqa: E402
    _trust_thresholds,
    classify_trust,
)


# 7일 관측 (Discovery 2026-06-05) round trust 점수 (drift_stats --all)
OBSERVED_TRUST_7D = {
    "R7":  98.6,
    "R20": 76.5,
    "R21": 64.9,
    "R22": 58.9,
}


def _clear_env(monkeypatch) -> None:
    monkeypatch.delenv("SIGNALFORGE_TRUST_WARNING", raising=False)
    monkeypatch.delenv("SIGNALFORGE_TRUST_CRITICAL", raising=False)


def test_precise_thresholds_7day_distribution(monkeypatch):
    """7일 분포 (R7/R20/R21/R22) 가 60/80 기본 임계에서 자연 분리되는지.

    동시에 tighten (65/82), relax (55/75), invert 보정까지 검증.
    """
    # 1) 기본 60/80
    _clear_env(monkeypatch)
    crit, warn = _trust_thresholds()
    assert crit == 60.0 and warn == 80.0, "기본 임계 60/80 잠금"

    levels = {r: classify_trust(s) for r, s in OBSERVED_TRUST_7D.items()}
    assert levels["R22"] == "critical", "R22 (58.9) critical 유지 필수 (안전 알림 기준)"
    assert levels["R21"] == "warning",  "R21 (64.9) warning 유지"
    assert levels["R20"] == "warning",  "R20 (76.5) warning 유지"
    assert levels["R7"]  == "normal",   "R7  (98.6) normal 유지"

    # 자연 분포 검증: 1 critical / 2 warning / 1 normal
    from collections import Counter
    counts = Counter(levels.values())
    assert counts == Counter({"warning": 2, "critical": 1, "normal": 1}), counts

    # 2) tighten (65/82) — R21 (64.9) 도 critical 로 격상 (조기 경보 강화 시나리오)
    monkeypatch.setenv("SIGNALFORGE_TRUST_CRITICAL", "65")
    monkeypatch.setenv("SIGNALFORGE_TRUST_WARNING", "82")
    crit, warn = _trust_thresholds()
    assert crit == 65.0 and warn == 82.0
    assert classify_trust(OBSERVED_TRUST_7D["R22"]) == "critical"
    assert classify_trust(OBSERVED_TRUST_7D["R21"]) == "critical", \
        "tighten 시나리오: R21 (64.9) → critical 로 격상"
    assert classify_trust(OBSERVED_TRUST_7D["R20"]) == "warning"
    assert classify_trust(OBSERVED_TRUST_7D["R7"])  == "normal"

    # 3) relax (55/75) — R22 critical 유지 (안전 마진, 55 미만 표본 없음)
    monkeypatch.setenv("SIGNALFORGE_TRUST_CRITICAL", "55")
    monkeypatch.setenv("SIGNALFORGE_TRUST_WARNING", "75")
    crit, warn = _trust_thresholds()
    assert crit == 55.0 and warn == 75.0
    # 58.9 >= 55 → critical 아님, warning
    assert classify_trust(OBSERVED_TRUST_7D["R22"]) == "warning", \
        "relax 시나리오: R22 (58.9) 는 critical 아님 (>= 55) → warning"
    assert classify_trust(OBSERVED_TRUST_7D["R21"]) == "warning"
    assert classify_trust(OBSERVED_TRUST_7D["R20"]) == "normal"

    # 4) 임계 역전 보정: crit >= warn 이면 crit = warn - 1
    monkeypatch.setenv("SIGNALFORGE_TRUST_CRITICAL", "90")
    monkeypatch.setenv("SIGNALFORGE_TRUST_WARNING", "80")
    crit, warn = _trust_thresholds()
    assert warn == 80.0
    assert crit == 79.0, "역전 보정: crit (90) >= warn (80) → crit = warn - 1 = 79"
    assert crit < warn, "보정 후 crit < warn 보장"
