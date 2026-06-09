"""WebSocket 채널 — 연결된 클라이언트 전체 broadcast.

connection manager 는 외부(api/alerts.py) 에서 주입한다.
manager 가 None 이면 dry-run (로그만).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.core.alerts.channels.base import AlertChannel

logger = logging.getLogger(__name__)


class WebsocketChannel(AlertChannel):
    name = "websocket"

    def __init__(self, manager: Optional[Any] = None) -> None:
        # manager: AlertConnectionManager (api/alerts.py)
        self.manager = manager

    def set_manager(self, manager: Any) -> None:
        self.manager = manager

    @property
    def dry_run(self) -> bool:
        return self.manager is None

    async def send(self, alert: Dict[str, Any]) -> bool:
        if self.dry_run:
            logger.info("[WS-DRY] %s", alert.get("rule"))
            return True
        try:
            await self.manager.broadcast(
                {"type": "alert", "data": alert}
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.exception("[WS] broadcast 실패: %s", exc)
            return False


__all__ = ["WebsocketChannel"]
