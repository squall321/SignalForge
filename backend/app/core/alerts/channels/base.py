"""채널 ABC.

알림 1건을 외부 시스템(Slack/Discord/WebSocket)으로 전송하는 추상 인터페이스.
구현체는 send() 만 책임지고, 룰 평가/이력 저장은 호출자(API/Celery)가 처리한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class AlertChannel(ABC):
    """알림 채널 ABC."""

    name: str = "base"

    @abstractmethod
    async def send(self, alert: Dict[str, Any]) -> bool:  # noqa: D401
        """알림 1건 전송. 성공/dry-run = True, 실제 실패만 False.

        alert dict 표준 키:
          - rule (str)        : 룰 이름
          - metric (str)      : metric_path
          - op (str)          : 비교 연산자
          - threshold (float) : 임계치
          - value (float)     : 측정값
          - severity (str)    : critical | warning | info
          - description (str|None) : 사람이 읽는 설명
          - fired_at (str ISO)
        """
        raise NotImplementedError


__all__ = ["AlertChannel"]
