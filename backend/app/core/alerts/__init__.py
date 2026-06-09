"""P4 트랙 A — 실시간 알림 코어.

룰엔진 (engine.py) + 채널 (channels/) 의 상위 패키지.
API/Celery 양쪽에서 import 한다.
"""
from app.core.alerts.engine import Rule, RuleEngine, RuleEvaluation, evaluate_metrics
from app.core.alerts.channels import (
    AlertChannel,
    SlackChannel,
    WebsocketChannel,
    DEFAULT_CHANNELS,
)

__all__ = [
    "Rule",
    "RuleEngine",
    "RuleEvaluation",
    "evaluate_metrics",
    "AlertChannel",
    "SlackChannel",
    "WebsocketChannel",
    "DEFAULT_CHANNELS",
]
