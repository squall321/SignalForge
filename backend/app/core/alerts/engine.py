"""룰엔진 — alert_rules 평가 + cooldown.

흐름:
1. RuleEngine.load_rules()          : DB 에서 활성 룰 메모리 로드 (또는 직접 list 주입)
2. evaluate_metrics()               : metric_path → scalar value 매핑 dict 를 받아 위반 룰 추출
3. RuleEngine.compare(rule, value)  : op 비교 (>, <, >=, <=, ==)
4. cooldown_sec 내 재발화 차단      : 메모리 캐시 (rule.id → last_fired_at)

DB 의존성을 분리하기 위해 evaluate_metrics() 는 순수 함수로 유지.
Celery worker / FastAPI 양쪽에서 같은 함수 사용.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


VALID_OPS = {">", "<", ">=", "<=", "=="}


@dataclass
class Rule:
    """alert_rules 1행 in-memory 표현."""

    id: int
    name: str
    metric_path: str
    op: str
    threshold: float
    severity: str = "warning"
    cooldown_sec: int = 900
    description: Optional[str] = None
    is_active: bool = True

    def validate(self) -> None:
        if self.op not in VALID_OPS:
            raise ValueError(f"Invalid op {self.op!r} (expected one of {VALID_OPS})")


@dataclass
class RuleEvaluation:
    """1 회 평가 결과."""

    rule: Rule
    value: float
    fired: bool
    payload: Dict[str, Any] = field(default_factory=dict)


def _compare(op: str, value: float, threshold: float) -> bool:
    if op == ">":
        return value > threshold
    if op == "<":
        return value < threshold
    if op == ">=":
        return value >= threshold
    if op == "<=":
        return value <= threshold
    if op == "==":
        return value == threshold
    raise ValueError(f"Invalid op {op!r}")


class RuleEngine:
    """룰 평가 + cooldown 매니저.

    싱글톤이 아니라 호출 측에서 인스턴스 보유 (FastAPI app.state 또는 Celery task local).
    cooldown 캐시는 in-memory dict — 분산 worker 환경이라면 Redis 로 교체.
    """

    def __init__(self, rules: Optional[Iterable[Rule]] = None) -> None:
        self.rules: List[Rule] = list(rules or [])
        # rule_id → last fired_at (epoch sec)
        self._last_fired: Dict[int, float] = {}

    def set_rules(self, rules: Iterable[Rule]) -> None:
        self.rules = list(rules)

    def is_cooled(self, rule: Rule, now_ts: Optional[float] = None) -> bool:
        """cooldown 만료 여부. True = 새로 발화 가능."""
        last = self._last_fired.get(rule.id)
        if last is None:
            return True
        ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        return (ts - last) >= rule.cooldown_sec

    def mark_fired(self, rule: Rule, now_ts: Optional[float] = None) -> None:
        ts = now_ts if now_ts is not None else datetime.now(timezone.utc).timestamp()
        self._last_fired[rule.id] = ts

    def evaluate(
        self,
        metrics: Dict[str, float],
        *,
        ignore_cooldown: bool = False,
    ) -> List[RuleEvaluation]:
        """metrics dict 와 보유 룰 매칭. fired==True 인 결과만 반환.

        Args:
            metrics: metric_path → scalar value
            ignore_cooldown: True 면 /test 같은 강제 호출에서 cooldown 무시
        """
        out: List[RuleEvaluation] = []
        now_ts = datetime.now(timezone.utc).timestamp()
        for rule in self.rules:
            if not rule.is_active:
                continue
            value = metrics.get(rule.metric_path)
            if value is None:
                continue
            triggered = _compare(rule.op, float(value), rule.threshold)
            if not triggered:
                continue
            if not ignore_cooldown and not self.is_cooled(rule, now_ts):
                continue
            out.append(
                RuleEvaluation(
                    rule=rule,
                    value=float(value),
                    fired=True,
                    payload={
                        "rule": rule.name,
                        "metric": rule.metric_path,
                        "op": rule.op,
                        "threshold": rule.threshold,
                        "value": float(value),
                        "severity": rule.severity,
                        "description": rule.description,
                        "fired_at": datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
                    },
                )
            )
            if not ignore_cooldown:
                self.mark_fired(rule, now_ts)
        return out


# ────────────────────────────────────────────────────────────────────
# 모듈 레벨 헬퍼 — Celery task / API 가 공통 사용
# ────────────────────────────────────────────────────────────────────
def evaluate_metrics(rules: Iterable[Rule], metrics: Dict[str, float]) -> List[RuleEvaluation]:
    """RuleEngine 인스턴스를 만들지 않고 1 회 평가 (cooldown 무시).

    Celery beat 가 자체 5분 주기로 호출하므로 cooldown 가드는
    DB(alert_events) last fired 조회로 처리 (tasks.py).
    """
    eng = RuleEngine(rules)
    return eng.evaluate(metrics, ignore_cooldown=True)


__all__ = ["Rule", "RuleEngine", "RuleEvaluation", "evaluate_metrics", "VALID_OPS"]
