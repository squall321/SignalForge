"""defect_anomaly_rule — 제품×결함 급등 탐지 룰 등록.

crawler/insight/defect_anomaly.py 가 이 행에서 severity·threshold·cooldown_sec 만
읽고 alert_events 에 직접 INSERT 한다(살아 있는 collection_health 와 동일 패턴).
RuleEngine 평가 경로는 타지 않는다 — 그 엔진은 전역 스칼라 1개 대 고정 임계값만
지원해서 (제품×부품×증상) 같은 엔티티 차원을 표현할 수 없다.

threshold 는 share 배수(ratio) 하한이다. 운영자가 PATCH /api/v1/alerts/rules 로
조정하면 코드 배포 없이 민감도가 바뀐다. is_active=false 로 즉시 중단도 가능하다.
"""
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        INSERT INTO alert_rules
            (name, metric_path, op, threshold, severity, cooldown_sec, description, is_active)
        VALUES (
            'defect_anomaly',
            'defect.share_ratio',
            '>=',
            2.0,
            'warning',
            21600,
            '제품×부품×증상 결함 점유율이 baseline 대비 급등(신제품은 이전 세대 동일 '
            '라이프사이클 구간과 비교, 단일 커뮤니티 쏠림은 유효 플랫폼 수로 배제)',
            TRUE
        )
        ON CONFLICT (name) DO NOTHING
    """)


def downgrade():
    op.execute("DELETE FROM alert_rules WHERE name = 'defect_anomaly'")
