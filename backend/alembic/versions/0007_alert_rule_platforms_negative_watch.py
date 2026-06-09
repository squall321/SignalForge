"""platforms_negative_share_watch — info 등급 사전 경고 룰.

Track C — 이중 thresholds 패턴.

배경:
  - rule 35 (platforms_negative_share, threshold=0.15, warning) 는 미발화 (현 metric=0.1094).
  - 7/64 플랫폼이 7d 평균 감성 < 0 → 한 플랫폼만 추가로 negative 화 되어도 0.125.
  - 운영자가 0.15 임계 도달 전에 추세를 인지할 수 있도록 *info* 등급 watch 룰 추가.

설계:
  - 동일 metric_path (community.platforms_negative_pct) — 추가 계산 비용 0.
  - threshold=0.08 (현 metric 0.1094 보다 낮음) → 이미 발화 상태로 시작.
    info 등급이라 Slack push 미발생 (alerts/dispatch 의 severity gating).
  - cooldown 3600s → 1 시간당 1 회 → 24/day, alert_events 부담 무시 가능.
  - rule 35 (warning) 와 협업: rule 36 이 7일간 매 시간 살아 있으면 추세 미상승,
    rule 35 까지 도달하면 *임계 전환*. 운영자가 grace period 동안 대응 가능.

Revision ID: 0007
Revises: 0006
"""
from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO alert_rules
            (name, metric_path, op, threshold, severity, cooldown_sec, description, is_active)
        VALUES (
            'platforms_negative_share_watch',
            'community.platforms_negative_pct',
            '>',
            0.08,
            'info',
            3600,
            '플랫폼 부정 비중 8%% 초과 — rule 35 (warning, 15%%) 사전 경고',
            true
        )
        ON CONFLICT (name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM alert_rules WHERE name = 'platforms_negative_share_watch'"
    )
