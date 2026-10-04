"""initial sales_service schema

Revision ID: a2c4e6f8b1d3
Revises:
Create Date: 2026-09-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = "a2c4e6f8b1d3"
down_revision = None
branch_labels = None
depends_on = None

order_status_enum = sa.Enum("draft", "confirmed", "fulfilled", "cancelled", name="orderstatus")


def upgrade() -> None:
    op.create_table(
        "sales_orders",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("order_number", sa.String(64), nullable=False),
        sa.Column("customer_name", sa.String(128), nullable=False),
        sa.Column("source_deal_id", sa.String(64), nullable=True),
        sa.Column("total_amount_usd", sa.Float(), nullable=False),
        sa.Column("status", order_status_enum, nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sales_orders_tenant_id", "sales_orders", ["tenant_id"])
    op.create_index("ix_sales_orders_order_number", "sales_orders", ["order_number"])

    op.create_table(
        "order_status_events",
        sa.Column("id", sa.CHAR(36), primary_key=True),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("order_id", sa.CHAR(36), sa.ForeignKey("sales_orders.id"), nullable=False),
        sa.Column("from_status", order_status_enum, nullable=False),
        sa.Column("to_status", order_status_enum, nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_order_status_events_tenant_id", "order_status_events", ["tenant_id"])
    op.create_index("ix_order_status_events_order_id", "order_status_events", ["order_id"])


def downgrade() -> None:
    op.drop_table("order_status_events")
    op.drop_table("sales_orders")
    order_status_enum.drop(op.get_bind(), checkfirst=True)
