"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── products ──────────────────────────────────────────
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(10), unique=True, nullable=False),
        sa.Column("series_code", sa.String(4), nullable=False),
        sa.Column("name_en", sa.String(100), nullable=False),
        sa.Column("name_ko", sa.String(100)),
        sa.Column("released_at", sa.Date()),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_products_code", "products", ["code"])
    op.create_index("idx_products_series", "products", ["series_code"])

    # ── platforms ─────────────────────────────────────────
    op.create_table(
        "platforms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(30), unique=True, nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("region", sa.String(10)),
        sa.Column("base_url", sa.String(255)),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
    )

    # ── voc_categories ────────────────────────────────────
    op.create_table(
        "voc_categories",
        sa.Column("code", sa.String(30), primary_key=True),
        sa.Column("name_en", sa.String(100)),
        sa.Column("name_ko", sa.String(100)),
        sa.Column("keywords", ARRAY(sa.Text())),
    )

    # ── voc_records ───────────────────────────────────────
    op.create_table(
        "voc_records",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id")),
        sa.Column("platform_id", sa.Integer(), sa.ForeignKey("platforms.id")),
        sa.Column("external_id", sa.String(200)),
        sa.Column("source_url", sa.String(1000)),
        sa.Column("author_name", sa.String(200)),
        sa.Column("content_original", sa.Text(), nullable=False),
        sa.Column("content_translated", sa.Text()),
        sa.Column("language_detected", sa.String(10)),
        sa.Column("country_code", sa.String(5)),
        sa.Column("sentiment_score", sa.Float()),
        sa.Column("sentiment_label", sa.String(20)),
        sa.Column("categories", ARRAY(sa.String(30))),
        sa.Column("likes_count", sa.Integer(), server_default="0"),
        sa.Column("comments_count", sa.Integer(), server_default="0"),
        sa.Column("shares_count", sa.Integer(), server_default="0"),
        sa.Column("engagement_score", sa.Float()),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("collected_at", sa.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("processed_at", sa.TIMESTAMP(timezone=True)),
        sa.UniqueConstraint("platform_id", "external_id", name="uq_platform_external"),
    )
    op.create_index("idx_voc_product", "voc_records", ["product_id", "collected_at"])
    op.create_index("idx_voc_country", "voc_records", ["country_code", "product_id"])
    op.create_index("idx_voc_sentiment", "voc_records", ["sentiment_label", "product_id"])
    op.execute(
        "CREATE INDEX idx_voc_categories ON voc_records USING GIN(categories)"
    )
    op.execute(
        "CREATE INDEX idx_voc_content_fts ON voc_records "
        "USING GIN(to_tsvector('english', COALESCE(content_translated, '')))"
    )

    # ── crawl_jobs ────────────────────────────────────────
    op.create_table(
        "crawl_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("platform_id", sa.Integer(), sa.ForeignKey("platforms.id")),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id")),
        sa.Column("status", sa.String(20), server_default="'pending'"),
        sa.Column("items_collected", sa.Integer(), server_default="0"),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("finished_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("idx_crawl_jobs_status", "crawl_jobs", ["status"])


def downgrade() -> None:
    op.drop_table("crawl_jobs")
    op.drop_table("voc_records")
    op.drop_table("voc_categories")
    op.drop_table("platforms")
    op.drop_table("products")
