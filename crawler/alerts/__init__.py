"""SignalForge 알림(Slack/Discord webhook) 모듈.

규칙 평가(rules)와 디스패치(dispatcher)를 분리한 단순 2-모듈 구성.
ALERT_WEBHOOK_URL 미설정 시 reports/alerts.log 에만 기록한다.
"""

from .dispatcher import send_alert  # noqa: F401
from .rules import check_all_rules  # noqa: F401
