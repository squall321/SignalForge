"""recalls platform seed — US CPSC 공식 리콜.
Revision ID: 0026
Revises: 0025
"""
from alembic import op
revision="0026"; down_revision="0025"; branch_labels=None; depends_on=None
def upgrade()->None:
    op.execute("""INSERT INTO platforms (code,name,region,base_url,is_active)
      VALUES ('recalls','Recalls (CPSC 리콜)','US','https://www.saferproducts.gov',true)
      ON CONFLICT (code) DO NOTHING""")
def downgrade()->None:
    op.execute("DELETE FROM platforms WHERE code='recalls'")
