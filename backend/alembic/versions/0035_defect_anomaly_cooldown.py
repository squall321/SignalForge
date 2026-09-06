"""defect_anomaly_cooldown — cooldown 을 beat 주기보다 길게.

0033 은 cooldown_sec=21600(6h)로 심었는데 beat 도 정확히 6h(crontab minute=20,
hour=*/6)라 두 값이 같았다. 판정식이 `now - fired_at < cooldown` 이므로 밀리초 지터가
발화/스킵을 결정하고, 스킵되면 다음 tick 은 12h 후라 반드시 발화한다 — 최대 억제율
50%. 동일 설계인 collection_health 실측에서 연속 발화쌍의 56.6%가 '바로 다음 tick'
이었다(cooldown 이 과반의 경우 아무것도 막지 못했다). 24h 로 올려 실제로 억제되게 한다.
"""
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE alert_rules SET cooldown_sec = 86400 "
               "WHERE name = 'defect_anomaly' AND cooldown_sec = 21600")


def downgrade():
    op.execute("UPDATE alert_rules SET cooldown_sec = 21600 "
               "WHERE name = 'defect_anomaly' AND cooldown_sec = 86400")
