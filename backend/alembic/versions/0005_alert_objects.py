"""P4 알림 객체 — alert_rules / alert_events

P4 트랙 A — 실시간 알림 (WebSocket + 룰엔진 + Slack dry-run).

객체:
- alert_rules  : 사용자/시스템 정의 룰 (metric_path, op, threshold, cooldown)
- alert_events : 룰 위반 시 발화 이력 (발화 시각, 측정값, 디스패치된 채널)

seed:
- 3 default 룰 (사용자가 운영 시작과 동시에 의미 있는 알림을 받도록)
  1) anomaly_z_high      : community.anomalies extreme_negative_7d 감지 (sent_avg_7d <= -0.3)
  2) negative_rate_spike : 일별 negative 비율 > 0.4
  3) new_term_spike      : insights.emerging top term spike >= 20 건/주

metric_path 는 RuleEngine 이 평가용 함수와 매핑하는 키 (literal string).
threshold 와 op 만으로 단순 비교가 가능하도록 metric → scalar value 추출은
RuleEngine 내부에서 처리한다.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1) alert_rules ──────────────────────────────────────────────────
    op.create_table(
        "alert_rules",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(64), nullable=False, unique=True),
        sa.Column("metric_path", sa.String(128), nullable=False),
        sa.Column("op", sa.String(4), nullable=False),  # '>', '<', '>=', '<=', '=='
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("severity", sa.String(16), nullable=False, server_default="warning"),
        sa.Column("cooldown_sec", sa.Integer, nullable=False, server_default="900"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("alert_rules_active_idx", "alert_rules", ["is_active"])

    # ── 2) alert_events ─────────────────────────────────────────────────
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "rule_id",
            sa.Integer,
            sa.ForeignKey("alert_rules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column(
            "payload",
            sa.dialects.postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "dispatched_channels",
            sa.dialects.postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
    )
    op.create_index("alert_events_rule_id_idx", "alert_events", ["rule_id"])
    op.create_index(
        "alert_events_fired_at_idx",
        "alert_events",
        [sa.text("fired_at DESC")],
    )

    # ── 3) seed default rules ───────────────────────────────────────────
    # metric_path 명세 (RuleEngine 이 이 키들을 알아야 함):
    #   community.extreme_negative_count : platforms/anomalies 응답 중
    #                                     reason='extreme_negative_7d' 카운트.
    #   community.negative_rate_max      : platforms/anomalies 응답 중
    #                                     drop_rate 의 ratio 최소값 → 1 - ratio.
    #                                     단순화: extreme_negative_7d 의 sent_avg_7d 최저값
    #                                     의 절댓값을 negative_rate proxy 로 사용.
    #   insights.new_term_spike_count    : insights/new-terms count_recent >= 20 개수.
    op.execute(
        """
        INSERT INTO alert_rules (name, metric_path, op, threshold, severity, cooldown_sec, description)
        VALUES
          (
            'anomaly_z_high',
            'community.extreme_negative_count',
            '>=',
            3,
            'critical',
            900,
            '플랫폼 3 곳 이상에서 7일 평균 감성이 -0.3 이하로 떨어진 상황'
          ),
          (
            'negative_rate_spike',
            'community.negative_rate_max',
            '>',
            0.4,
            'warning',
            900,
            '특정 플랫폼의 부정 감성 비율이 0.4 (40%) 를 넘은 상황'
          ),
          (
            'new_term_spike',
            'insights.new_term_spike_count',
            '>=',
            20,
            'warning',
            900,
            '최근 7일에 처음 등장하면서 누적 20건 이상인 신조어 1개 이상 존재'
          )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS alert_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS alert_rules CASCADE;")
