"""operations_monitor — R14 트랙 E.

매시 30분 호출되는 crawler.insight.operations_monitor 가 운영 SLO 6개를
점검하여 위반 시 이 룰의 alert_events 행을 INSERT 한다.

설계:
  - metric_path: system.ops_violations (단순 카운트 — RuleEngine 평가 대상 아님)
  - op '>', threshold=0 — 단순 카운트 > 0 일 때 위반.
  - severity=warning — 6 점검 중 critical 인 회귀 위반은 alert_events.severity 에 직접 'critical' 기록.
  - cooldown_sec 3600 — 매시 +30 호출이라 1 시간 = 1 회. 동일 사이클의 다중 위반은
    같은 호출 안에서 cooldown 가드 후 일괄 INSERT (multi-row).
  - is_active=true.

Revision ID: 0017
Revises: 0016
"""
from alembic import op


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO alert_rules
            (name, metric_path, op, threshold, severity, cooldown_sec, description, is_active)
        VALUES (
            'operations_monitor',
            'system.ops_violations',
            '>',
            0,
            'warning',
            3600,
            'R14 운영 1주 모니터링 — 매시 30분 SLO 위반 (data_quality / regression / voc / sentiment / topic / grounding) 시 발화',
            true
        )
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM alert_rules WHERE name = 'operations_monitor'"
    )
