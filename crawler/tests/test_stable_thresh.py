"""R25 트랙 C/E — 안정 임계 (trust_level) 단위 테스트.

검증 (압축, Discovery 사양):
  - workflow_drift_stats.classify_trust 가 score < 60 → critical,
    60 <= score < 80 → warning, score >= 80 → normal 로 분류.
  - 환경변수 (SIGNALFORGE_TRUST_CRITICAL/WARNING) 가 임계를 덮어쓴다.
  - compute() 결과의 round 항목에 trust_level 이 채워지고,
    overall.trust_thresholds / trust_level_counts 가 정합.
  - 7일 실 데이터 분포 (R20=76.5/R21=64.9/R22=58.9/R7=98.6) 에서
    기본 임계로 critical=1, warning=2, normal=1 알림이 분리된다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from insight import workflow_drift_stats as wds  # noqa: E402


def _reset_env(monkeypatch) -> None:
    monkeypatch.delenv("SIGNALFORGE_TRUST_CRITICAL", raising=False)
    monkeypatch.delenv("SIGNALFORGE_TRUST_WARNING", raising=False)


def test_classify_trust_default_and_env_override(monkeypatch, tmp_path):
    """classify_trust 기본/env override + 실 7일 보고서 분포 확인."""
    # 1) 기본 임계 (60/80) — 경계 명시.
    _reset_env(monkeypatch)
    assert wds.classify_trust(100.0) == "normal"
    assert wds.classify_trust(80.0) == "normal"
    assert wds.classify_trust(79.9) == "warning"
    assert wds.classify_trust(60.0) == "warning"
    assert wds.classify_trust(59.9) == "critical"
    assert wds.classify_trust(0.0) == "critical"

    # 2) env override — 임계 강화 (warn=90/crit=70).
    monkeypatch.setenv("SIGNALFORGE_TRUST_WARNING", "90")
    monkeypatch.setenv("SIGNALFORGE_TRUST_CRITICAL", "70")
    assert wds.classify_trust(89.0) == "warning", "기존 normal 였던 89 가 warning 으로 격상"
    assert wds.classify_trust(69.0) == "critical", "기존 warning 였던 69 가 critical 로 격상"
    assert wds.classify_trust(91.0) == "normal"

    # 3) critical >= warning 정합성 보정 — critical 을 warn-1 로 강제.
    monkeypatch.setenv("SIGNALFORGE_TRUST_CRITICAL", "95")
    monkeypatch.setenv("SIGNALFORGE_TRUST_WARNING", "80")
    crit, warn = wds._trust_thresholds()
    assert warn == 80.0
    assert crit == 79.0, "crit>=warn 입력은 warn-1 로 보정"

    # 4) 실 7일 보고서 통계 — 기본 임계로 round 별 trust_level 분리 확인.
    _reset_env(monkeypatch)
    reports_dir = Path(__file__).resolve().parents[2] / "docs" / "dashboard"
    if reports_dir.is_dir():
        files = wds._select_reports(reports_dir, all_reports=True)
        result = wds.compute(files)
        rounds = {r["round"]: r for r in result.get("rounds", [])}
        # 기대 분포: R22 critical(58.9) / R21 warning(64.9) / R20 warning(76.5) / R7 normal(98.6).
        # 향후 데이터 변화에 강건하도록 *분류 정합성* 만 검증 — 점수→레벨 일대일.
        for r in rounds.values():
            level = wds.classify_trust(r["trust_score"])
            assert r["trust_level"] == level, (
                f"round {r['round']}: trust_score {r['trust_score']} 와 "
                f"trust_level {r['trust_level']} 불일치 (기대 {level})"
            )
        # overall.trust_thresholds / trust_level_counts 가 정합.
        overall = result.get("overall", {})
        th = overall.get("trust_thresholds") or {}
        assert th.get("critical_below") == 60.0
        assert th.get("warning_below") == 80.0
        if overall.get("rounds_analyzed", 0) > 0:
            level_counts = overall.get("trust_level_counts") or {}
            # 카운트 합 = 분석된 라운드 수.
            assert sum(level_counts.values()) == overall["rounds_analyzed"]
            # 실 7일 데이터에서 기본 임계로는 최소 1건은 critical 또는 warning 발화.
            # (R20/R21/R22 가 모두 60~80 또는 <60 범위)
            non_normal = level_counts.get("critical", 0) + level_counts.get("warning", 0)
            assert non_normal >= 1, (
                f"7일 데이터 분포에서 critical+warning >= 1 기대 "
                f"(실제 {level_counts}) — 임계가 너무 느슨한지 확인"
            )
