"""채널 패키지 — Slack / WebSocket.

DEFAULT_CHANNELS 는 settings 기반 dry-run 기본 인스턴스를 만들어
Celery task 와 API 가 공유할 수 있도록 제공한다.
"""
from app.core.alerts.channels.base import AlertChannel
from app.core.alerts.channels.slack import SlackChannel
from app.core.alerts.channels.websocket import WebsocketChannel


def _build_default_channels() -> dict[str, AlertChannel]:
    """settings 기반 기본 채널 (Slack + WS). 키 없으면 dry-run."""
    from app.config import settings

    slack = SlackChannel(
        webhook_url=getattr(settings, "SLACK_WEBHOOK_URL", "") or "",
        channel=getattr(settings, "SLACK_CHANNEL", "") or "",
    )
    # WebsocketChannel 은 manager 를 외부에서 주입받지 않으면 broadcast 가 동작 안 함.
    # api/alerts.py 가 manager 를 wire 한다.
    ws = WebsocketChannel(manager=None)
    return {"slack": slack, "websocket": ws}


DEFAULT_CHANNELS: dict[str, AlertChannel] = _build_default_channels()


__all__ = [
    "AlertChannel",
    "SlackChannel",
    "WebsocketChannel",
    "DEFAULT_CHANNELS",
]
