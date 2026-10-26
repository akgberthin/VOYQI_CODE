"""agents: key unique per tenant, not globally

Revision ID: c4e7a1d2f9b3
Revises: b6a5eefecaf7
Create Date: 2026-10-26

The initial schema made agents.key globally unique, so an agent could be
registered (and paused) for only one tenant. The constraint was created
unnamed: Postgres named it agents_key_key; SQLite needs a naming
convention to find it.
"""
from alembic import op

revision = "c4e7a1d2f9b3"
down_revision = "b6a5eefecaf7"
branch_labels = None
depends_on = None

SQLITE_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _old_unique_name() -> str:
    return "agents_key_key" if op.get_bind().dialect.name == "postgresql" else "uq_agents_key"


def upgrade() -> None:
    with op.batch_alter_table("agents", naming_convention=SQLITE_NAMING) as batch:
        batch.drop_constraint(_old_unique_name(), type_="unique")
        batch.create_unique_constraint("uq_agents_tenant_key", ["tenant_id", "key"])
        batch.create_index("ix_agents_key", ["key"])


def downgrade() -> None:
    with op.batch_alter_table("agents", naming_convention=SQLITE_NAMING) as batch:
        batch.drop_index("ix_agents_key")
        batch.drop_constraint("uq_agents_tenant_key", type_="unique")
        batch.create_unique_constraint(_old_unique_name(), ["key"])
